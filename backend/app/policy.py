from __future__ import annotations

import hashlib
import re
import shlex

from .models import PolicyPreviewResponse, PolicyViolation


# Dangerous patterns (PDF + common agent risks)
_DENY_RES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"rm\s+-rf\s+/(?!\s)"), "recursive delete on root or dangerous path"),
    (re.compile(r"rm\s+.*--no-preserve-root"), "rm with no-preserve-root"),
    (re.compile(r"mkfs\.|dd\s+if=/dev/zero"), "destructive disk operations"),
    (re.compile(r"iptables\s+-F|ufw\s+disable"), "disabling firewall"),
    (re.compile(r">\s*/dev/sd|of=/dev/"), "raw block device writes"),
    (re.compile(r"curl\s+[^\n]*\|\s*(ba)?sh"), "curl pipe to shell"),
    (re.compile(r"wget\s+[^\n]*\|\s*(ba)?sh"), "wget pipe to shell"),
    (re.compile(r";\s*rm\s+-rf"), "chained destructive rm"),
]

# Allowlist prefixes for demo (conservative)
ALLOW_PREFIXES = (
    "docker ",
    "docker compose ",
    "echo ",
    "sleep ",
    "#",
)


def _lines_from_script(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        lines.append(raw.rstrip())
    return lines if lines else [text.strip()]


def _check_line(line: str) -> str | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    for pat, reason in _DENY_RES:
        if pat.search(stripped):
            return reason
    lower = stripped.lower()
    for pat, reason in _DENY_RES:
        if pat.search(lower):
            return reason
    # Allowlist: must start with allowed prefix
    ok = any(stripped.startswith(pref) for pref in ALLOW_PREFIXES)
    if not ok:
        return "command not on allowlist (only docker/echo/sleep/comments for demo)"
    # Extra: block kubectl exec to prod-like (PDF example)
    if re.search(r"kubectl\s+exec", stripped):
        return "kubectl exec blocked by policy"
    if "rm -rf" in stripped and "/tmp" not in stripped:
        # allow only very narrow rm if needed — block broad rm
        if re.search(r"rm\s+-rf\s+/", stripped):
            return "recursive delete under / blocked"
    return None


def preview_policy(script: str) -> PolicyPreviewResponse:
    original = _lines_from_script(script)
    sanitized: list[str] = []
    blocked: list[PolicyViolation] = []
    for i, line in enumerate(original, start=1):
        reason = _check_line(line)
        if reason:
            blocked.append(PolicyViolation(line_number=i, line=line, reason=reason))
            sanitized.append(f"# BLOCKED: {reason}")
        else:
            sanitized.append(line)
    return PolicyPreviewResponse(
        original_lines=original,
        sanitized_lines=sanitized,
        blocked=blocked,
    )


def hash_content(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def jit_check_line(line: str) -> str | None:
    """Re-check immediately before execution."""
    return _check_line(line)


def parse_executable_lines(sanitized_script: str) -> list[str]:
    """Return lines to execute as argv-capable strings (one shell line each)."""
    out: list[str] = []
    for line in sanitized_script.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("# BLOCKED:"):
            continue
        out.append(line.rstrip())
    return out


def split_line_argv(line: str) -> list[str] | None:
    """Parse a single line into argv for subprocess; returns None if not parseable."""
    try:
        parts = shlex.split(line, posix=True)
    except ValueError:
        return None
    if not parts:
        return None
    return parts
