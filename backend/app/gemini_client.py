from __future__ import annotations

import asyncio
import os
import re

from .models import PolicyPreviewResponse
from .policy import hash_content, preview_policy


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


def _extract_runbook(text: str) -> str:
    fence = re.search(r"```(?:bash|sh|yaml|yml)?\s*\n([\s\S]*?)```", text)
    if fence:
        return fence.group(1).strip()
    return text.strip()


def _extract_analysis(text: str) -> str:
    """Split off the analysis portion (before the code block)."""
    parts = re.split(r"```(?:bash|sh)?\s*\n", text, maxsplit=1)
    return parts[0].strip() if len(parts) > 1 else text.strip()


async def analyze_and_runbook(
    *,
    incident_description: str,
    log_excerpt: str,
    metrics_hint: str,
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
    analysis_parts.append("📋 AI Analysis (Local Engine — Gemini API unavailable):\n")

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
