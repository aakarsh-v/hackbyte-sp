from __future__ import annotations

import asyncio
import base64
import io
import os
import re

from .models import LogEvent, PolicyPreviewResponse
from .policy import hash_content, preview_policy

_MAX_IMAGE_BYTES = 5 * 1024 * 1024
_ALLOWED_IMAGE_MIMES = frozenset(
    {"image/png", "image/jpeg", "image/jpg", "image/webp", "image/gif"}
)


def _get_model():
    import google.generativeai as genai

    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("GEMINI_API_KEY is not set")
    genai.configure(api_key=key)
    model_name = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
    return genai.GenerativeModel(model_name)


def _sync_generate(prompt: str) -> str:
    model = _get_model()
    resp = model.generate_content(prompt)
    return (getattr(resp, "text", None) or "").strip()


def _sync_generate_with_image(
    prompt: str,
    *,
    image_base64: str,
    image_mime_type: str,
) -> str:
    from PIL import Image

    mime = (image_mime_type or "image/png").strip().lower()
    if mime == "image/jpg":
        mime = "image/jpeg"
    if mime not in _ALLOWED_IMAGE_MIMES:
        raise ValueError(f"unsupported image MIME type: {image_mime_type}")
    raw = base64.b64decode(image_base64.strip())
    if len(raw) > _MAX_IMAGE_BYTES:
        raise ValueError("image too large (max 5MB)")
    img = Image.open(io.BytesIO(raw))
    model = _get_model()
    resp = model.generate_content([prompt, img])
    return (getattr(resp, "text", None) or "").strip()


def _extract_runbook(text: str) -> str:
    fence = re.search(r"```(?:bash|sh|yaml|yml)?\s*\n([\s\S]*?)```", text)
    if fence:
        return fence.group(1).strip()
    return text.strip()


def _extract_analysis(text: str) -> str:
    """Split off the analysis portion (before the code block)."""
    parts = re.split(r"```(?:bash|sh)?\s*\n", text, maxsplit=1)
    return parts[0].strip() if len(parts) > 1 else text.strip()


def compress_log_lines_for_prompt(
    events: list[LogEvent], *, max_lines: int, max_msg_len: int
) -> str:
    """Compact one line per event for token-efficient incident-context prompts."""
    if max_lines < 1:
        return ""
    tail = events[-max_lines:]
    lines: list[str] = []
    for e in tail:
        msg = (e.message or "").replace("\n", " ").replace("|", "/").strip()
        if len(msg) > max_msg_len:
            msg = msg[: max_msg_len - 1] + "…"
        lvl = (e.level or "INFO").upper()
        lines.append(f"{e.time}|{e.service}|{lvl}|{msg}")
    return "\n".join(lines)


def heuristic_incident_context(compressed: str) -> str:
    """Short incident blurb when GEMINI_API_KEY is unset (demo-friendly, no API cost)."""
    rows = [ln for ln in compressed.splitlines() if ln.strip()]
    if not rows:
        return "[Auto context] No log lines yet. Ingest logs to build incident context."

    services: set[str] = set()
    err = warn = 0
    themes: list[str] = []
    for ln in rows:
        parts = ln.split("|", 3)
        if len(parts) >= 3:
            services.add(parts[1])
            lvl = parts[2].upper()
            if lvl in ("ERROR", "FATAL", "CRITICAL"):
                err += 1
            elif lvl == "WARN" or lvl == "WARNING":
                warn += 1
        low = ln.lower()
        if "503" in low or "502" in low or "bad gateway" in low:
            themes.append("upstream/gateway errors")
        if "timeout" in low or "timed out" in low:
            themes.append("timeouts")
        if "connection refused" in low or "unavailable" in low:
            themes.append("connectivity failures")
        if "circuit" in low:
            themes.append("circuit breaker")
        if "pool" in low and "exhaust" in low:
            themes.append("connection pool pressure")
        if "oom" in low or "137" in low or "killed" in low:
            themes.append("OOM/process kill")

    svc_list = ", ".join(sorted(services)[:8])
    if len(services) > 8:
        svc_list += f", +{len(services) - 8} more"
    theme_s = "; ".join(dict.fromkeys(themes)) if themes else "general service activity"

    return (
        f"[Auto context — heuristic, no Gemini] Recent activity across {len(services)} service(s): {svc_list}. "
        f"In this window: {err} error-level line(s), {warn} warning-level line(s). "
        f"Themes: {theme_s}. Set GEMINI_API_KEY for AI-written incident narrative."
    )


async def summarize_for_incident_context(compressed: str) -> str:
    """
    3–5 sentence incident description for the Analyze text box.
    Token-efficient: caller passes pre-compressed lines only.
    """
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        return heuristic_incident_context(compressed)

    prompt = f"""You are an on-call SRE. Below are COMPRESSED production log lines (format: time|service|LEVEL|message).
Write a concise incident description (3 to 5 sentences) for an "Incident context" text box before root-cause analysis.
Name specific services and failure types seen in the lines. Do not write a runbook or shell commands.
Plain text only, no markdown, no bullet list.

LOG LINES:
{compressed or "(empty)"}
"""
    text = await asyncio.to_thread(_sync_generate, prompt)
    return (text or "").strip() or heuristic_incident_context(compressed)


async def analyze_and_runbook(
    *,
    incident_description: str,
    log_excerpt: str,
    metrics_hint: str,
    image_base64: str = "",
    image_mime_type: str = "image/png",
) -> tuple[str, str, PolicyPreviewResponse, str]:

    prompt = f"""You are a world-class Senior Site Reliability Engineer (SRE) at a Fortune 500 company.
Your job is to analyze production incidents with precision and generate actionable, specific remediation runbooks.

═══════════════════════════════════════════
INCIDENT REPORT
═══════════════════════════════════════════
{incident_description or "(see logs below for incident details)"}

═══════════════════════════════════════════
LIVE LOG EVIDENCE (most recent first)
═══════════════════════════════════════════
{log_excerpt or "(no logs captured)"}

═══════════════════════════════════════════
ADDITIONAL METRICS / CONTEXT
═══════════════════════════════════════════
{metrics_hint or "(none provided)"}

If an image is attached, use it (e.g. Grafana/dashboard screenshots, error dialogs, architecture diagrams) to inform root-cause analysis.

First, explain the likely root cause and reasoning (step by step).

═══════════════════════════════════════════
ANALYSIS INSTRUCTIONS
═══════════════════════════════════════════
1. Carefully read the log evidence above. Identify the EXACT error lines.
2. Trace the failure cascade: what failed first → what broke as a result.
3. Identify the ROOT CAUSE (database? OOM kill? network partition? crash loop?).
4. Write a technical analysis paragraph with specific log evidence citations.
5. Rate the severity: P1 (site down) / P2 (degraded) / P3 (minor).

═══════════════════════════════════════════
RUNBOOK INSTRUCTIONS
═══════════════════════════════════════════
Generate a PRECISE, STEP-BY-STEP remediation bash script that SPECIFICALLY addresses the failure you identified in the logs.

RULES FOR THE RUNBOOK:
- Each step must directly fix an issue identified in the logs above.
- Use ONLY these allowed commands: docker, docker compose, echo, sleep, aws, systemctl
- Do NOT use: rm -rf /, curl | sh, wget | sh, iptables -F, mkfs, dd
- If the logs show an EC2/cloud issue → use: aws ec2 or docker commands
- If the logs show a database connection issue → check and restart db container
- If the logs show OOM kill (exit 137) → investigate memory, restart with more headroom
- If the logs show Nginx 502 → restart the upstream service Nginx is pointing to
- Add an echo "✅ Step N done" after each step to confirm progress
- The runbook MUST be specific to the actual incident, NOT generic

Output format:
1. First write your detailed technical analysis (no code block yet)
2. Then write the runbook inside ONE ```bash ... ``` block
"""
    img = (image_base64 or "").strip()
    if img:
        full = await asyncio.to_thread(
            _sync_generate_with_image,
            prompt,
            image_base64=img,
            image_mime_type=image_mime_type or "image/png",
        )
    else:
        full = await asyncio.to_thread(_sync_generate, prompt)
    analysis = _extract_analysis(full)
    raw_runbook = _extract_runbook(full)

    preview = preview_policy(raw_runbook)
    sanitized = "\n".join(preview.sanitized_lines)
    h = hash_content(sanitized)
    return analysis, raw_runbook, preview, h


def fallback_template(
    incident_description: str, log_excerpt: str
) -> tuple[str, str, PolicyPreviewResponse, str]:
    """
    Smart fallback: parse the log excerpt ourselves to generate a contextual runbook
    instead of the generic 'docker restart payment-service'.
    """
    log_lower = log_excerpt.lower()
    incident_lower = incident_description.lower()

    # Detect error patterns and build specific remediation
    steps: list[str] = []
    analysis_parts: list[str] = []

    analysis_parts.append(f"🔍 Incident: {incident_description}\n")
    analysis_parts.append(
        "[Fallback] 📋 AI Analysis (Local Engine — Gemini API unavailable):\n"
    )

    # EC2 / Nginx 502 pattern
    if "502" in log_lower or "bad gateway" in log_lower:
        analysis_parts.append(
            "  • ROOT CAUSE: Nginx is returning 502 Bad Gateway, indicating the upstream "
            "application server is not responding. This is typically caused by the upstream "
            "service crashing or refusing connections on its bound port."
        )
        steps += [
            "# Step 1: Confirm the upstream service status",
            "echo '🔍 Checking upstream service health...'",
            "docker inspect payment-service --format='Status: {{.State.Status}} | Health: {{.State.Health.Status}}'",
            "# Step 2: Restart the crashed upstream service",
            "echo '🔄 Restarting payment-service (upstream for Nginx)...'",
            "docker compose restart payment-service",
            "echo '✅ Step 2 done — payment-service restarted'",
            "sleep 3",
        ]

    # Database connection refused pattern
    if "db:5432" in log_lower or "connection refused" in log_lower or "connectexception" in log_lower:
        analysis_parts.append(
            "  • ROOT CAUSE: Database connection refused on port 5432. The application "
            "cannot reach the PostgreSQL instance. The DB container may have crashed or "
            "restarted and the application did not reconnect."
        )
        steps += [
            "# Step 3: Restart database service to restore connectivity",
            "echo '🔄 Restarting PostgreSQL database container...'",
            "docker compose restart db",
            "echo '✅ Step 3 done — DB restarted'",
            "sleep 5",
        ]

    # OOM Kill (exit code 137) pattern
    if "137" in log_lower or "oom" in log_lower or "killed" in log_lower:
        analysis_parts.append(
            "  • ROOT CAUSE: Process killed with exit code 137 (SIGKILL). This indicates "
            "an Out-Of-Memory (OOM) kill event. The container exceeded its memory limit "
            "and the kernel terminated it to protect system stability."
        )
        steps += [
            "# Step 4: Restart OOM-killed service with extended memory",
            "echo '⚠️ OOM kill detected — restarting killed service...'",
            "docker compose restart payment-service",
            "echo '✅ Step 4 done — service restarted after OOM kill'",
            "sleep 3",
        ]

    # EC2 / systemd pattern
    if "ec2:" in log_lower or "systemd" in log_lower or "systemctl" in log_lower:
        analysis_parts.append(
            "  • CONTEXT: Logs originate from AWS EC2 instance. The systemd service "
            "manager reports process failures, indicating this is a production EC2 environment."
        )
        steps += [
            "# Step 5: Verify all services are back up",
            "echo '🏥 Final health check across all services...'",
            "docker compose ps",
            "echo '✅ Remediation complete — monitor for 60s to confirm stability'",
        ]

    # Generic fallback if no patterns found
    if not steps:
        analysis_parts.append(
            "  • Unable to identify specific root cause from log excerpt. "
            "Applying general service recovery procedure."
        )
        steps = [
            "# Step 1: Check all container statuses",
            "echo '🔍 Checking all service statuses...'",
            "docker compose ps",
            "# Step 2: Restart affected services",
            "echo '🔄 Restarting all services...'",
            "docker compose restart",
            "echo '✅ All services restarted'",
        ]

    analysis_parts.append(
        f"\n📊 Severity: {'P1 — Service Down' if '502' in log_lower or '137' in log_lower else 'P2 — Service Degraded'}"
    )

    analysis = "\n".join(analysis_parts)
    raw = "\n".join(steps)

    preview = preview_policy(raw)
    sanitized = "\n".join(preview.sanitized_lines)
    return analysis, raw, preview, hash_content(sanitized)


def incident_query_fallback(
    question: str, log_excerpt: str, runbook_excerpt: str
) -> str:
    """Tiny heuristic summary when GEMINI_API_KEY is unset (demo-friendly)."""
    lines = [ln for ln in log_excerpt.splitlines() if ln.strip()]
    n = len(lines)
    err = sum(
        1
        for ln in lines
        if "ERROR" in ln or "FATAL" in ln or "CRITICAL" in ln
    )
    pay = sum(1 for ln in lines if "payment" in ln.lower())
    qlow = question.lower()
    bits = [
        "[Local] GEMINI_API_KEY is not set — heuristic summary only, not a full NL answer.",
        f"Log lines in excerpt: {n}.",
        f"Lines containing ERROR/FATAL/CRITICAL: {err}.",
        f"Lines mentioning 'payment' (case-insensitive): {pay}.",
    ]
    if "crash" in qlow or "crash" in log_excerpt.lower():
        crashish = sum(
            1
            for ln in lines
            if "crash" in ln.lower() or "panic" in ln.lower() or "137" in ln
        )
        bits.append(f"Lines suggesting crash/OOM/panic (heuristic): {crashish}.")
    if runbook_excerpt.strip():
        bits.append(
            f"Recent runbook snippets (truncated) were available ({len(runbook_excerpt)} chars)."
        )
    bits.append(
        "Set GEMINI_API_KEY for full natural-language answers grounded in the excerpt."
    )
    return "\n".join(bits)


async def answer_incident_question(
    *,
    question: str,
    log_excerpt: str,
    runbook_excerpt: str = "",
) -> str:
    """Answer a plain-English question using only provided logs/runbook text."""
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        return incident_query_fallback(question, log_excerpt, runbook_excerpt)

    prompt = f"""You assist on-call engineers. Answer ONLY using the LOG EXCERPT and optional RUNBOOK SNIPPETS below.
Rules:
- If the question needs data not present (e.g. exact MTTR, duration to fix, or a time range with no timestamps in logs), say clearly that the excerpt is insufficient and what is missing.
- Prefer quoting or paraphrasing log lines as evidence. Do not invent incident counts or timelines.
- The runbook table has no per-row timestamps; do not infer fix duration from it.

QUESTION:
{question.strip()}

LOG EXCERPT (lines include time when the system recorded it):
{log_excerpt or "(no logs in excerpt)"}
"""
    if (runbook_excerpt or "").strip():
        prompt += f"""
RECENT RUNBOOK SNIPPETS (truncated; no timing metadata in database):
{runbook_excerpt.strip()}
"""
    return await asyncio.to_thread(_sync_generate, prompt)
