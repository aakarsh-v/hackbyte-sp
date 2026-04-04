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
    dbn = _database_name()
    body = json.dumps([session_id, last_sanitized, last_sanitized_hash])
    c = _client()
    r = await c.post(
        f"/v1/database/{dbn}/call/upsert_session_runbook",
        content=body.encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    r.raise_for_status()


def _looks_like_uuid(s: str) -> bool:
    s = s.strip()
    return len(s) == 36 and s.count("-") == 4


def _is_sha256_hex(s: str) -> bool:
    if len(s) != 64:
        return False
    try:
        int(s, 16)
    except ValueError:
        return False
    return True


def _cell_as_id_int(v: Any) -> int | None:
    """Parse auto-inc row id; None if cell is session_id, hash, script, etc."""
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(v) if v.is_integer() else None
    s = str(v).strip()
    if s in ("", "null", "None"):
        return None
    if "\n" in s or "\r" in s:
        return None
    # Auto-inc u64 fits in <= 20 decimal digits; longer strings are scripts / blobs.
    if len(s) > 24:
        return None
    if _looks_like_uuid(s):
        return None
    if _is_sha256_hex(s):
        return None
    s_num = s.replace(",", "").replace("_", "")
    try:
        return int(s_num, 10)
    except ValueError:
        pass
    if 1 <= len(s_num) <= 16 and all(c in "0123456789abcdefABCDEF" for c in s_num):
        try:
            n = int(s_num, 16)
            if n < 2**64:
                return n
        except ValueError:
            pass
    try:
        f = float(s_num)
        if f.is_integer():
            return int(f)
    except (ValueError, TypeError, OverflowError):
        pass
    return None


def _infer_id_column_index(rows: list[list[Any]]) -> int:
    """Prefer the column that parses as id for every row; else the best-scoring column."""
    if not rows:
        return 0
    ncols = len(rows[0])
    nrows = len(rows)
    best_j = 0
    best_key: tuple[int, int, int] = (-1, -1, -1)
    for j in range(ncols):
        parsed: list[int] = []
        for r in rows:
            if len(r) <= j:
                continue
            n = _cell_as_id_int(r[j])
            if n is not None:
                parsed.append(n)
        if not parsed:
            continue
        score = len(parsed)
        full = 1 if score == nrows else 0
        mx = max(parsed)
        key = (full, score, mx)
        if key > best_key:
            best_key = key
            best_j = j
    return best_j


def _latest_runbook_row(rows: list[list[Any]], id_idx: int) -> list[Any] | None:
    """Latest row = max id among rows where the id cell parses."""
    scored: list[tuple[int, list[Any]]] = []
    for r in rows:
        if len(r) <= id_idx:
            continue
        n = _cell_as_id_int(r[id_idx])
        if n is not None:
            scored.append((n, r))
    if not scored:
        return None
    return max(scored, key=lambda t: t[0])[1]


def _runbook_column_indices(rows: list[list[Any]], session_id: str) -> tuple[int, int, int]:
    """
    Map SELECT * cells to (id_idx, sanitized_idx, hash_idx).
    Prefer structural hints: session_id cell, 64-char SHA-256 hash, then id vs script.
    Falls back to numeric id inference when shape is ambiguous.
    """
    first_row = rows[0]
    n = len(first_row)

    sid_idx: int | None = next(
        (i for i in range(n) if str(first_row[i]).strip() == session_id.strip()),
        None,
    )
    hash_idx: int | None = next(
        (i for i in range(n) if _is_sha256_hex(str(first_row[i]).strip())),
        None,
    )

    if n == 4 and sid_idx is not None and hash_idx is not None:
        remaining = [i for i in range(4) if i not in (sid_idx, hash_idx)]
        if len(remaining) == 2:
            a, b = remaining[0], remaining[1]
            na, nb = _cell_as_id_int(first_row[a]), _cell_as_id_int(first_row[b])
            if na is not None and nb is None:
                return a, b, hash_idx
            if nb is not None and na is None:
                return b, a, hash_idx
            la, lb = len(str(first_row[a])), len(str(first_row[b]))
            id_idx, sanitized_idx = (a, b) if la <= lb else (b, a)
            return id_idx, sanitized_idx, hash_idx

    id_idx = _infer_id_column_index(rows)

    sid_idx2: int | None = None
    for i, cell in enumerate(first_row):
        if i == id_idx:
            continue
        if str(cell).strip() == session_id.strip():
            sid_idx2 = i
            break

    others = [i for i in range(len(first_row)) if i != id_idx]
    if sid_idx2 is not None:
        others = [i for i in others if i != sid_idx2]

    if len(others) == 2:
        a, b = others[0], others[1]
        va, vb = str(first_row[a]), str(first_row[b])
        if _is_sha256_hex(va) and not _is_sha256_hex(vb):
            return id_idx, b, a
        if _is_sha256_hex(vb) and not _is_sha256_hex(va):
            return id_idx, a, b
        return (id_idx, a, b) if len(va) >= len(vb) else (id_idx, b, a)

    non_id = [i for i in range(len(first_row)) if i != id_idx]
    hash_f = next((i for i in non_id if _is_sha256_hex(str(first_row[i]))), non_id[-1])
    sanitized_f = next((i for i in non_id if i != hash_f), non_id[0])
    return id_idx, sanitized_f, hash_f


async def get_session_runbook(session_id: str) -> tuple[str | None, str | None]:
    """Return the latest runbook for the session (max ``id``), or (None, None)."""
    c = _client()
    esc = _escape_sql_string(session_id)
    attempts = [
        f"SELECT * FROM session_runbook WHERE session_id = '{esc}' ORDER BY id DESC LIMIT 1",
        f"SELECT * FROM session_runbook WHERE session_id = '{esc}'",
    ]
    rows: list[list[Any]] | None = None
    for sql in attempts:
        r = await c.post(
            f"/v1/database/{_database_name()}/sql",
            content=sql.encode("utf-8"),
            headers={"Content-Type": "text/plain"},
        )
        if r.status_code == 400:
            continue
        r.raise_for_status()
        data = r.json()
        block = data[0].get("rows") if data else None
        if block:
            rows = block
            break
    if not rows:
        return None, None
    id_idx, sanitized_idx, hash_idx = _runbook_column_indices(rows, session_id)
    row = _latest_runbook_row(rows, id_idx)
    if row is None and rows:
        row = rows[-1]
    if row is None:
        return None, None
    last_sanitized = str(row[sanitized_idx])
    last_hash = str(row[hash_idx])
    return last_sanitized, last_hash
