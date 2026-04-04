from __future__ import annotations

import os
import time

from .models import LogEvent

ERROR_LEVELS: frozenset[str] = frozenset({"ERROR", "FATAL", "CRITICAL"})
ERROR_PATTERNS: tuple[str, ...] = (
    "exception",
    "panic",
    "oom",
    "out of memory",
    "crash",
    "timeout",
    "timed out",
    "unavailable",
    "connection refused",
    "failed",
    "traceback",
    "stack trace",
    "segfault",
    "killed",
    "502",
    "503",
    "504",
)

COOLDOWN_SECONDS: float = float(os.getenv("AUTO_ANALYZE_COOLDOWN", "120"))
MIN_ERROR_BURST: int = int(os.getenv("AUTO_ANALYZE_MIN_BURST", "1"))
_BURST_WINDOW: float = 30.0  # seconds: burst counter resets if no errors within this window

# Module-level debounce state (in-process; resets on restart which is fine)
_last_trigger: float = 0.0
_error_burst: int = 0
_last_error_time: float = 0.0


def _is_error_event(event: LogEvent) -> bool:
    if event.level.upper() in ERROR_LEVELS:
        return True
    msg = event.message.lower()
    return any(pat in msg for pat in ERROR_PATTERNS)


def should_trigger(event: LogEvent) -> bool:
    """Return True exactly once per cooldown window when an incident is detected.

    Thread/coroutine safety: FastAPI runs on a single-threaded asyncio event loop,
    so these plain globals are safe without locks.
    """
    global _last_trigger, _error_burst, _last_error_time

    if not _is_error_event(event):
        return False

    now = time.monotonic()

    # Reset burst counter if too much time passed since the last error
    if now - _last_error_time > _BURST_WINDOW:
        _error_burst = 0

    _error_burst += 1
    _last_error_time = now

    # Haven't accumulated enough errors yet
    if _error_burst < MIN_ERROR_BURST:
        return False

    # Still within the cooldown window from the last trigger
    if now - _last_trigger < COOLDOWN_SECONDS:
        return False

    # Fire
    _last_trigger = now
    _error_burst = 0
    return True


def reset() -> None:
    """Reset all debounce state. Useful in tests."""
    global _last_trigger, _error_burst, _last_error_time
    _last_trigger = 0.0
    _error_burst = 0
    _last_error_time = 0.0
