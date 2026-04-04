"""Tests for POST /incident-query (natural language over logs)."""

from __future__ import annotations

from starlette.testclient import TestClient

from app.main import app


def test_incident_query_empty_question_returns_400():
    with TestClient(app, raise_server_exceptions=True) as client:
        r = client.post("/incident-query", json={"question": "   "})
        assert r.status_code == 400


def test_incident_query_fallback_when_no_gemini(client, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    client.post(
        "/ingest",
        json={
            "service": "payment-service",
            "level": "ERROR",
            "message": "connection refused",
        },
    )
    r = client.post(
        "/incident-query",
        json={"question": "How many errors mention payment?", "log_limit": 100},
    )
    assert r.status_code == 200
    data = r.json()
    assert "answer" in data
    assert "[Local]" in data["answer"]
    assert "Log lines in excerpt" in data["answer"]
