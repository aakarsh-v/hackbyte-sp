"""
CloudWatch Logs poller — pulls real log events from AWS CloudWatch Log Groups
(e.g. EC2 instance logs shipped via the CloudWatch Agent) and injects them
into the DevOps AI platform pipeline as regular LogEvent objects.

Activated when CW_LOG_GROUP env var is set.
Requires AWS credentials via any standard boto3 mechanism:
  - Environment vars: AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION
  - IAM role attached to the host EC2 instance / ECS task
  - ~/.aws/credentials (local dev)
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timezone
from typing import Callable, Coroutine, Any

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _cfg_group() -> str | None:
    return os.environ.get("CW_LOG_GROUP", "").strip() or None

def _cfg_stream_prefix() -> str:
    """Optional: filter to streams that start with this prefix (e.g. 'i-' for EC2)."""
    return os.environ.get("CW_LOG_STREAM_PREFIX", "").strip()

def _cfg_region() -> str:
    return os.environ.get("AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))

def _cfg_interval() -> float:
    return float(os.environ.get("CW_POLL_INTERVAL_S", "15"))

def _cfg_lookback() -> int:
    """On first start, how many seconds back to fetch (default 5 min)."""
    return int(os.environ.get("CW_INITIAL_LOOKBACK_S", "300"))

def _cfg_max_events() -> int:
    return int(os.environ.get("CW_MAX_EVENTS_PER_POLL", "200"))


# ---------------------------------------------------------------------------
# Level inference from message text
# ---------------------------------------------------------------------------

_LEVEL_KEYWORDS = {
    "ERROR": ["error", "exception", "traceback", "critical", "fatal", "fail"],
    "WARN":  ["warn", "warning", "deprecated", "caution"],
    "DEBUG": ["debug", "trace", "verbose"],
}

def _infer_level(message: str) -> str:
    lower = message.lower()
    for level, keywords in _LEVEL_KEYWORDS.items():
        if any(k in lower for k in keywords):
            return level
    return "INFO"


# ---------------------------------------------------------------------------
# Service name inference from log stream name
# ---------------------------------------------------------------------------

def _infer_service(log_stream_name: str, log_group: str) -> str:
    """Derive a human-readable service name from stream/group name."""
    # EC2 streams often look like: i-0abc123def456/var/log/syslog
    # ECS streams: ecs/container-name/task-id
    # Try EC2 instance id pattern
    parts = log_stream_name.split("/")
    if parts and parts[0].startswith("i-"):
        return f"ec2:{parts[0]}"
    if len(parts) >= 2 and parts[0] == "ecs":
        return f"ecs:{parts[1]}"
    # Fall back to group name base
    group_base = log_group.rstrip("/").split("/")[-1]
    return group_base or "cloudwatch"


# ---------------------------------------------------------------------------
# Poller
# ---------------------------------------------------------------------------

class CloudWatchPoller:
    """
    Background asyncio task that polls a CloudWatch Logs group and injects
    events into the platform via an async callback.
    """

    def __init__(
        self,
        on_event: Callable[..., Coroutine[Any, Any, None]],
    ) -> None:
        self._on_event = on_event
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    # ------------------------------------------------------------------ #

    def start(self) -> None:
        if _cfg_group() is None:
            print("[CloudWatchPoller] CW_LOG_GROUP not set — cloud log ingestion disabled.")
            return
        print(f"[CloudWatchPoller] ✅ STARTING — group={_cfg_group()} region={_cfg_region()} interval={_cfg_interval():.0f}s")
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="cw-poller")

    def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()

    # ------------------------------------------------------------------ #

    async def _run(self) -> None:
        try:
            import boto3  # type: ignore
        except ImportError:
            logger.error("CloudWatchPoller: boto3 not installed — cannot poll CloudWatch.")
            return

        log_group = _cfg_group()
        region = _cfg_region()
        interval = _cfg_interval()
        max_events = _cfg_max_events()
        stream_prefix = _cfg_stream_prefix()
        lookback_ms = _cfg_lookback() * 1000

        # boto3 client — runs blocking calls in executor
        client = boto3.client("logs", region_name=region)

        # Timestamp of last event we've seen (ms since epoch)
        start_time_ms = int(time.time() * 1000) - lookback_ms

        logger.info("CloudWatchPoller: first poll from %s ms ago", lookback_ms)

        while not self._stop.is_set():
            try:
                parsed_events, new_start = await asyncio.to_thread(
                    self._poll_once,
                    client,
                    log_group,
                    stream_prefix,
                    start_time_ms,
                    max_events,
                )
                if new_start > start_time_ms:
                    start_time_ms = new_start
                # Process events on the async side (no event loop issues)
                for ev in parsed_events:
                    await self._on_event(
                        ev["service"], ev["level"], ev["time"], ev["message"], {"stream": ev["stream"]}
                    )
            except asyncio.CancelledError:
                break
            except Exception as exc:
                print(f"[CloudWatchPoller] ⚠️ poll error: {exc}")

            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval)
                break  # stop event was set
            except asyncio.TimeoutError:
                pass  # normal — keep looping

        print("[CloudWatchPoller] stopped.")

    # ------------------------------------------------------------------ #

    def _poll_once(
        self,
        client: Any,
        log_group: str,
        stream_prefix: str,
        start_time_ms: int,
        max_events: int,
    ) -> int:
        """Blocking boto3 call (runs in thread pool). Returns (parsed_events, newest_ts)."""
        kwargs: dict[str, Any] = {
            "logGroupName": log_group,
            "startTime": start_time_ms + 1,  # exclusive
            "limit": max_events,
        }
        if stream_prefix:
            kwargs["logStreamNamePrefix"] = stream_prefix

        resp = client.filter_log_events(**kwargs)
        raw_events = resp.get("events", [])

        if not raw_events:
            return [], start_time_ms

        parsed: list[dict] = []
        newest_ts = start_time_ms
        for evt in raw_events:
            ts_ms: int = evt.get("timestamp", 0)
            message: str = evt.get("message", "").strip()
            stream: str = evt.get("logStreamName", "")
            if not message:
                continue

            service = _infer_service(stream, log_group)
            level = _infer_level(message)
            dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
            time_str = dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

            parsed.append({"service": service, "level": level, "time": time_str, "message": message, "stream": stream})
            if ts_ms > newest_ts:
                newest_ts = ts_ms

        print(f"[CloudWatchPoller] ✅ Ingested {len(parsed)} events from {log_group}")
        return parsed, newest_ts
