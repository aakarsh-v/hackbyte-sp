from __future__ import annotations

import json
import os
from typing import Any

import httpx

from .models import LogEvent

_http: httpx.AsyncClient | None = None


def set_http_client(client: httpx.AsyncClient | None) -> None:
    global _http
    _http = client


def _database_name() -> str:
    return os.environ.get("SPACETIME_DATABASE", "devopsai")


def _client() -> httpx.AsyncClient:
    if _http is None:
        raise RuntimeError("HTTP client not configured (app lifespan did not run)")
    return _http


def _extra_to_json(extra: dict[str, Any] | None) -> str:
    if not extra:
        return "{}"
    return json.dumps(extra, separators=(",", ":"))


def row_to_log_event(cols: list[Any]) -> LogEvent:
    _id, time, service, level, message, extra_json = cols
    extra: dict[str, Any] | None
    if extra_json and str(extra_json) != "{}":
        try:
            extra = json.loads(str(extra_json))
        except json.JSONDecodeError:
            extra = None
    else:
        extra = None
    return LogEvent(
        time=str(time),
        service=str(service),
        level=str(level),
        message=str(message),
        extra=extra,
    )


def _escape_sql_string(s: str) -> str:
    return s.replace("'", "''")


async def append_log_event(event: LogEvent) -> None:
    body = json.dumps(
        [
            event.time,
            event.service,
            event.level,
            event.message,
            _extra_to_json(event.extra),
        ]
    )
    c = _client()
    r = await c.post(
        f"/v1/database/{_database_name()}/call/ingest_log",
        content=body.encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    r.raise_for_status()


async def fetch_log_tail(limit: int) -> list[LogEvent]:
    """Return up to `limit` events in chronological order (oldest first)."""
    c = _client()
    r = await c.post(
        f"/v1/database/{_database_name()}/sql",
        content=b"SELECT * FROM log_event",
        headers={"Content-Type": "text/plain"},
    )
    r.raise_for_status()
    data = r.json()
    if not data:
        return []
    rows: list[list[Any]] = data[0].get("rows") or []
    rows.sort(key=lambda row: int(row[0]))
    tail = rows[-limit:] if len(rows) > limit else rows
    return [row_to_log_event(row) for row in tail]


async def upsert_session_runbook(
    *,
    session_id: str,
    last_sanitized: str,
    last_sanitized_hash: str,
) -> None:
    body = json.dumps([session_id, last_sanitized, last_sanitized_hash])
    c = _client()
    r = await c.post(
        f"/v1/database/{_database_name()}/call/upsert_session_runbook",
        content=body.encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    r.raise_for_status()


async def get_session_runbook(session_id: str) -> tuple[str | None, str | None]:
    c = _client()
    sql = (
        "SELECT * FROM session_runbook WHERE session_id = '"
        + _escape_sql_string(session_id)
        + "'"
    )
    r = await c.post(
        f"/v1/database/{_database_name()}/sql",
        content=sql.encode("utf-8"),
        headers={"Content-Type": "text/plain"},
    )
    r.raise_for_status()
    data = r.json()
    if not data or not data[0].get("rows"):
        return None, None
    row = data[0]["rows"][0]
    _sid, last_sanitized, last_hash = row[0], row[1], row[2]
    return str(last_sanitized), str(last_hash)
