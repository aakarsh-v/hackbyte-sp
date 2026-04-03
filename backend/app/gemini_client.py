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


async def analyze_and_runbook(
    *,
    incident_description: str,
    log_excerpt: str,
    metrics_hint: str,
) -> tuple[str, str, PolicyPreviewResponse, str]:
    prompt = f"""You are a DevOps SRE assistant. Analyze the incident and propose a fix.

Incident description:
{incident_description or "(see logs below)"}

Recent logs:
{log_excerpt or "(none)"}

Metrics / notes:
{metrics_hint or "(none)"}

First, explain the likely root cause and reasoning (step by step).

Then output a bash script in a SINGLE fenced code block ```bash ... ``` that ONLY uses commands allowed:
- docker compose restart <service>
- docker restart <container_name>
- echo ... / sleep ... for logging
Do NOT use rm, curl | sh, iptables, kubectl exec, or any destructive commands.

Comment each line with # explaining why it is safe.
"""
    full = await asyncio.to_thread(_sync_generate, prompt)
    analysis = full
    raw_runbook = _extract_runbook(full)
    if raw_runbook == full.strip() and "```" in full:
        parts = re.split(r"```(?:bash|sh)?\s*\n", full, maxsplit=1)
        if len(parts) > 1:
            analysis = parts[0].strip()

    preview = preview_policy(raw_runbook)
    sanitized = "\n".join(preview.sanitized_lines)
    h = hash_content(sanitized)
    return analysis, raw_runbook, preview, h


def fallback_template(incident_description: str, log_excerpt: str) -> tuple[str, str, PolicyPreviewResponse, str]:
    analysis = (
        f"[Fallback] Incident: {incident_description}\n"
        "Likely service overload or dependency failure based on log keywords.\n"
        "Suggested next step: restart the affected container via docker compose."
    )
    raw = (
        "# Restart payment service (example)\n"
        "docker restart payment-service\n"
    )
    preview = preview_policy(raw)
    sanitized = "\n".join(preview.sanitized_lines)
    return analysis, raw, preview, hash_content(sanitized)
