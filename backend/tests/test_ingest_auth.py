"""Optional INGEST_SECRET on POST /ingest."""

from __future__ import annotations

from starlette.testclient import TestClient

from app.main import app


def test_ingest_without_secret_allows_post(client):
    r = client.post(
        "/ingest",
        json={
            "service": "t",
            "level": "INFO",
            "message": "ok",
        },
    )
    assert r.status_code == 200


def test_ingest_rejects_wrong_secret_when_configured(monkeypatch):
    monkeypatch.setenv("INGEST_SECRET", "test-secret-value")
    with TestClient(app, raise_server_exceptions=True) as c:
        r = c.post(
            "/ingest",
            json={"service": "t", "level": "INFO", "message": "x"},
        )
        assert r.status_code == 401
        r2 = c.post(
            "/ingest",
            headers={"X-Ingest-Secret": "wrong"},
            json={"service": "t", "level": "INFO", "message": "x"},
        )
        assert r2.status_code == 401
        r3 = c.post(
            "/ingest",
            headers={"X-Ingest-Secret": "test-secret-value"},
            json={"service": "t", "level": "INFO", "message": "ok"},
        )
        assert r3.status_code == 200
