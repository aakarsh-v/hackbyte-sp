"""
demo-replay.py  —  Replay realistic multi-service logs into the DevOps AI backend.

Usage (PowerShell / bash):
  python scripts/demo-replay.py                          # full scenario, speed=5
  python scripts/demo-replay.py --scenario cascade       # just the failure phase
  python scripts/demo-replay.py --scenario full --speed 0  # fire all instantly
  python scripts/demo-replay.py --url http://localhost:8000 --scenario degradation --speed 2

Scenario choices:
  healthy      – 15 INFO lines, all services up (good baseline)
  degradation  – 20 WARN lines, DB slow queries, latency rising
  cascade      – 25 ERROR lines, DB crash → payment failure → circuit breaker
  recovery     – 10 INFO lines, services coming back online
  full         – all 4 phases in sequence (default)

Speed:
  0  – fire all lines instantly (no delay)
  N  – divide the original inter-line gap by N  (e.g. speed=5 is 5x faster)
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# Force UTF-8 output on Windows so banner characters render correctly.
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf-8-sig"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ── ANSI colours (disabled on Windows if not in a capable terminal) ──────────
def _colour_supported() -> bool:
    if sys.platform == "win32":
        try:
            import ctypes
            kernel = ctypes.windll.kernel32  # type: ignore[attr-defined]
            # Enable ENABLE_VIRTUAL_TERMINAL_PROCESSING (0x0004) on stdout
            kernel.SetConsoleMode(kernel.GetStdHandle(-11), 7)
            return True
        except Exception:
            return False
    return True

USE_COLOUR = _colour_supported()

RESET  = "\033[0m"  if USE_COLOUR else ""
BOLD   = "\033[1m"  if USE_COLOUR else ""
GREEN  = "\033[92m" if USE_COLOUR else ""
YELLOW = "\033[93m" if USE_COLOUR else ""
RED    = "\033[91m" if USE_COLOUR else ""
CYAN   = "\033[96m" if USE_COLOUR else ""
DIM    = "\033[2m"  if USE_COLOUR else ""

LEVEL_COLOUR = {
    "INFO":     GREEN,
    "WARN":     YELLOW,
    "WARNING":  YELLOW,
    "ERROR":    RED,
    "CRITICAL": RED,
    "FATAL":    RED,
}

PHASE_COLOUR = {
    "healthy":     GREEN,
    "degradation": YELLOW,
    "cascade":     RED,
    "recovery":    CYAN,
}

# ── Phase boundaries (by "phase" key in extra, or fall back to line order) ───
PHASE_ORDER = ["healthy", "degradation", "cascade", "recovery"]

SCENARIO_PHASES: dict[str, list[str]] = {
    "healthy":     ["healthy"],
    "degradation": ["degradation"],
    "cascade":     ["cascade"],
    "recovery":    ["recovery"],
    "full":        PHASE_ORDER,
}


def load_jsonl(path: Path) -> list[dict]:
    events: list[dict] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events


def filter_by_phases(events: list[dict], phases: list[str]) -> list[dict]:
    wanted = set(phases)
    return [
        e for e in events
        if e.get("extra", {}).get("phase", "healthy") in wanted
    ]


def original_timestamps(events: list[dict]) -> list[datetime]:
    """Parse the 'time' field from each event (UTC)."""
    result: list[datetime] = []
    for e in events:
        t = e.get("time", "")
        try:
            dt = datetime.fromisoformat(t.replace("Z", "+00:00"))
        except ValueError:
            dt = datetime.now(tz=timezone.utc)
        result.append(dt)
    return result


def compute_delays(timestamps: list[datetime], speed: float) -> list[float]:
    """Return per-event sleep durations (seconds) based on original gaps / speed."""
    if speed == 0 or len(timestamps) < 2:
        return [0.0] * len(timestamps)
    delays: list[float] = [0.0]
    for i in range(1, len(timestamps)):
        gap = (timestamps[i] - timestamps[i - 1]).total_seconds()
        gap = max(gap, 0.0)
        delays.append(gap / speed)
    return delays


def now_utc_iso() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def post_event(url: str, event: dict, secret: str = "") -> bool:
    payload = json.dumps(event).encode("utf-8")
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if secret:
        headers["X-Ingest-Secret"] = secret
    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            return resp.status == 200
    except urllib.error.HTTPError as exc:
        print(f"  {RED}HTTP {exc.code}{RESET} — backend rejected event")
        return False
    except Exception as exc:
        print(f"  {RED}Connection error:{RESET} {exc}")
        return False


def fmt_event(event: dict, idx: int, total: int) -> str:
    level   = event.get("level", "INFO").upper()
    service = event.get("service", "?")
    message = event.get("message", "")
    phase   = event.get("extra", {}).get("phase", "")
    lc      = LEVEL_COLOUR.get(level, "")
    pc      = PHASE_COLOUR.get(phase, "")
    counter = f"{DIM}[{idx:>3}/{total}]{RESET}"
    phase_tag = f"{pc}[{phase}]{RESET} " if phase else ""
    return (
        f"  {counter} {lc}{BOLD}{level:<8}{RESET} "
        f"{CYAN}{service:<22}{RESET} {phase_tag}{message}"
    )


def print_phase_banner(phase: str) -> None:
    pc = PHASE_COLOUR.get(phase, "")
    label = {
        "healthy":     "PHASE 1 -- HEALTHY  (normal traffic)",
        "degradation": "PHASE 2 -- DEGRADATION  (latency rising, DB slow queries)",
        "cascade":     "PHASE 3 -- CASCADE FAILURE  (DB crash -> circuit breaker -> 503s)",
        "recovery":    "PHASE 4 -- RECOVERY  (services coming back online)",
    }.get(phase, phase.upper())
    width = 72
    bar = "-" * width
    print(f"\n{pc}{BOLD}{bar}{RESET}")
    print(f"{pc}{BOLD}  {label}{RESET}")
    print(f"{pc}{BOLD}{bar}{RESET}\n")


def run(
    scenario: str,
    speed: float,
    ingest_url: str,
    secret: str,
    jsonl_path: Path,
    dry_run: bool,
) -> None:
    events = load_jsonl(jsonl_path)
    phases = SCENARIO_PHASES.get(scenario, PHASE_ORDER)
    events = filter_by_phases(events, phases)

    if not events:
        print(f"{RED}No events found for scenario '{scenario}' in {jsonl_path}{RESET}")
        sys.exit(1)

    timestamps = original_timestamps(events)
    delays     = compute_delays(timestamps, speed)
    total      = len(events)

    print(f"\n{BOLD}{'='*72}{RESET}")
    print(f"{BOLD}  DevOps AI -- Demo Log Replayer{RESET}")
    print(f"{BOLD}{'='*72}{RESET}")
    print(f"  Scenario : {CYAN}{scenario}{RESET}")
    print(f"  Events   : {CYAN}{total}{RESET}")
    print(f"  Speed    : {CYAN}{'instant' if speed == 0 else f'{speed}x'}{RESET}")
    print(f"  Target   : {CYAN}{ingest_url}{RESET}")
    if dry_run:
        print(f"  {YELLOW}DRY-RUN mode — events will NOT be posted{RESET}")
    print()

    success_count = 0
    current_phase: str | None = None

    for i, (event, delay) in enumerate(zip(events, delays), start=1):
        # Show phase banner on transition
        phase = event.get("extra", {}).get("phase", "")
        if phase and phase != current_phase:
            current_phase = phase
            print_phase_banner(phase)

        # Sleep between events (skip first)
        if delay > 0:
            time.sleep(delay)

        # Rewrite timestamp to now so UI shows live data
        event_to_send = dict(event)
        event_to_send["time"] = now_utc_iso()

        print(fmt_event(event, i, total))

        if not dry_run:
            ok = post_event(ingest_url, event_to_send, secret)
            if ok:
                success_count += 1
        else:
            success_count += 1

    print(f"\n{BOLD}{'='*72}{RESET}")
    colour = GREEN if success_count == total else YELLOW
    print(
        f"  {colour}{BOLD}Done!{RESET}  "
        f"{success_count}/{total} events ingested successfully."
    )
    if not dry_run and success_count == total:
        print(
            f"\n  {GREEN}All events sent.{RESET}  "
            "Open the console UI and click  'Analyze + Runbook'  to see AI analysis!"
        )
    print(f"{BOLD}{'='*72}{RESET}\n")


def main() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    default_jsonl = repo_root / "samples" / "realistic-demo.jsonl"
    default_url   = os.environ.get("BASE_URL", "http://localhost:8000")
    default_secret = os.environ.get("INGEST_SECRET", "")

    parser = argparse.ArgumentParser(
        description="Replay realistic demo logs into the DevOps AI backend.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--scenario", "-s",
        choices=list(SCENARIO_PHASES.keys()),
        default="full",
        help="Which phase(s) to replay (default: full)",
    )
    parser.add_argument(
        "--speed", "-x",
        type=float,
        default=5.0,
        help="Replay speed multiplier: 0=instant, 1=real-time, 5=5x faster (default: 5)",
    )
    parser.add_argument(
        "--url", "-u",
        default=default_url,
        help=f"Backend ingest URL (default: {default_url})",
    )
    parser.add_argument(
        "--secret",
        default=default_secret,
        help="X-Ingest-Secret header value (reads INGEST_SECRET env var by default)",
    )
    parser.add_argument(
        "--file", "-f",
        type=Path,
        default=default_jsonl,
        help=f"Path to JSONL file (default: {default_jsonl})",
    )
    parser.add_argument(
        "--dry-run", "-n",
        action="store_true",
        help="Print events without posting to backend",
    )

    args = parser.parse_args()
    ingest_url = args.url.rstrip("/") + "/ingest"

    if not args.file.exists():
        print(f"{RED}ERROR: JSONL file not found: {args.file}{RESET}")
        sys.exit(1)

    run(
        scenario=args.scenario,
        speed=args.speed,
        ingest_url=ingest_url,
        secret=args.secret,
        jsonl_path=args.file,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
