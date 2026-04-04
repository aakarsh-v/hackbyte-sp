from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.prometheus_snapshot import build_metrics_snapshot


@pytest.mark.asyncio
async def test_build_metrics_snapshot_queries_prometheus(monkeypatch):
    mock_body = {
        "status": "success",
        "data": {"resultType": "vector", "result": []},
    }

    async def fake_get(self, url, params=None):
        r = MagicMock()
        r.status_code = 200

        def j():
            return mock_body

        r.json = j
        return r

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        get = fake_get

    monkeypatch.setattr("app.prometheus_snapshot.httpx.AsyncClient", FakeClient)

    text = await build_metrics_snapshot("http://prometheus:9090")
    assert "--- Prometheus snapshot ---" in text
    assert "up:" in text
    assert "success" in text


@pytest.mark.asyncio
async def test_analyze_merges_prometheus_snapshot(client, monkeypatch):
    captured: dict[str, str] = {}

    async def fake_analyze(**kwargs):
        captured["metrics_hint"] = kwargs.get("metrics_hint", "")
        raw = "echo ok"
        from app.policy import hash_content, preview_policy

        preview = preview_policy(raw)
        h = hash_content("\n".join(preview.sanitized_lines))
        return ("a", raw, preview, h)

    monkeypatch.setattr("app.gemini_client.analyze_and_runbook", fake_analyze)

    async def snap(_base: str):
        return "--- Prometheus snapshot ---\nup: {\"status\":\"success\"}"

    monkeypatch.setattr("app.main.build_metrics_snapshot", snap)
    monkeypatch.setenv("PROMETHEUS_URL", "http://prometheus:9090")

    r = client.post(
        "/analyze",
        json={
            "incident_description": "test",
            "include_logs": False,
            "include_metrics_hint": "user pasted hint",
            "include_prometheus_snapshot": True,
        },
    )
    assert r.status_code == 200
    assert "user pasted hint" in captured["metrics_hint"]
    assert "--- Prometheus snapshot ---" in captured["metrics_hint"]
