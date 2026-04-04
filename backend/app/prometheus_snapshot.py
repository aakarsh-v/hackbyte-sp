"""Fetch compact instant-query results from Prometheus for LLM context (Executive Summary: metrics-aware analysis)."""

from __future__ import annotations

import json
from typing import Any

import httpx

# Instant queries aligned with infra/grafana/dashboards and service metrics
_SNAPSHOT_QUERIES: tuple[tuple[str, str], ...] = (
    ("up", "up"),
    ("up_by_job", "sum by (job) (up)"),
    ("http_request_rate", "sum(rate(http_request_duration_ms_count[5m]))"),
)


def _trim_json(data: Any, max_len: int = 2500) -> str:
    s = json.dumps(data, separators=(",", ":"))
    if len(s) <= max_len:
        return s
    return s[: max_len - 3] + "..."


async def build_metrics_snapshot(base_url: str, *, timeout: float = 8.0) -> str:
    """Return a text block for merging into the Gemini metrics hint."""
    base = base_url.rstrip("/")
    lines: list[str] = ["--- Prometheus snapshot ---"]
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            for label, query in _SNAPSHOT_QUERIES:
                try:
                    r = await client.get(
                        f"{base}/api/v1/query",
                        params={"query": query},
                    )
                    if r.status_code != 200:
                        lines.append(f"{label}: HTTP {r.status_code}")
                        continue
                    payload = r.json()
                    status = payload.get("status", "?")
                    if status != "success":
                        lines.append(f"{label}: status={status}")
                        continue
                    lines.append(f"{label}: {_trim_json(payload)}")
                except Exception as ex:  # noqa: BLE001 — snapshot is best-effort
                    lines.append(f"{label}: error {ex!s}")
    except Exception as ex:  # noqa: BLE001
        lines.append(f"Prometheus unreachable: {ex!s}")
    return "\n".join(lines)
