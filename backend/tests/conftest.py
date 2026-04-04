from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from app.main import app
from app.models import LogEvent


@pytest.fixture
def mock_store() -> dict:
    return {"logs": [], "runbooks": {}}


@pytest.fixture
def patched_persistence(mock_store: dict, monkeypatch: pytest.MonkeyPatch):
    async def append_log_event(event: LogEvent) -> None:
        mock_store["logs"].append(event)

    async def fetch_log_tail(limit: int) -> list[LogEvent]:
        rows = mock_store["logs"]
        return rows[-limit:] if len(rows) > limit else rows

    async def append_session_runbook(
        *,
        session_id: str,
        last_sanitized: str,
        last_sanitized_hash: str,
    ) -> None:
        mock_store["runbooks"].setdefault(session_id, []).append(
            (last_sanitized, last_sanitized_hash)
        )

    async def get_session_runbook(session_id: str) -> tuple[str | None, str | None]:
        hist: list = mock_store["runbooks"].get(session_id) or []
        if not hist:
            return None, None
        return hist[-1]

    async def fetch_recent_runbook_summaries(limit: int) -> str:
        return ""

    monkeypatch.setattr("app.main.append_log_event", append_log_event)
    monkeypatch.setattr("app.main.fetch_log_tail", fetch_log_tail)
    monkeypatch.setattr("app.main.append_session_runbook", append_session_runbook)
    monkeypatch.setattr("app.main.get_session_runbook", get_session_runbook)
    monkeypatch.setattr("app.main.fetch_recent_runbook_summaries", fetch_recent_runbook_summaries)


@pytest.fixture
def client(patched_persistence):
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c
