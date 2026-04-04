from __future__ import annotations

from app.policy import hash_content, preview_policy


def test_policy_preview_blocks_aws_key_pattern(client):
    r = client.post(
        "/policy/preview",
        json={"script": 'echo leak AKIA0123456789ABCDEF\n'},
    )
    assert r.status_code == 200
    data = r.json()
    assert len(data["blocked"]) >= 1


def test_policy_preview_blocks_dangerous_line(client):
    r = client.post(
        "/policy/preview",
        json={"script": "rm -rf /\necho safe_line"},
    )
    assert r.status_code == 200
    data = r.json()
    assert len(data["blocked"]) >= 1
    assert any("# BLOCKED:" in line for line in data["sanitized_lines"])


def test_analyze_execute_mocked_gemini_blocks_rm_runs_echo(client, monkeypatch):
    async def fake_analyze(**kwargs):
        raw = "rm -rf /\necho day2_ok"
        preview = preview_policy(raw)
        sanitized = "\n".join(preview.sanitized_lines)
        h = hash_content(sanitized)
        return ("step by step analysis", raw, preview, h)

    async def stub_execute_lines(lines, *, allow_docker=True):
        # Avoid real subprocess (non-portable `echo` on Windows); assert unsafe lines were stripped.
        joined = "\n".join(lines)
        assert "rm" not in joined
        assert any("echo" in x.lower() for x in lines)
        yield '$ echo day2_ok\nexit=0\nday2_ok'

    monkeypatch.setattr("app.gemini_client.analyze_and_runbook", fake_analyze)
    monkeypatch.setattr("app.main.execute_lines", stub_execute_lines)

    r = client.post(
        "/analyze",
        json={
            "incident_description": "Payment 503 errors",
            "include_logs": False,
            "include_metrics_hint": "",
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert len(data["preview"]["blocked"]) >= 1

    sanitized = "\n".join(data["preview"]["sanitized_lines"])
    approved_hash = data["approved_hash"]
    r2 = client.post(
        "/execute",
        json={"content": sanitized, "content_hash": approved_hash},
    )
    assert r2.status_code == 200
    body = r2.json()
    combined = "\n".join(body["output"])
    assert "day2_ok" in combined
    assert "rm -rf" not in combined


def test_analyze_fallback_when_gemini_unavailable(client, monkeypatch):
    async def fail(**kwargs):
        raise RuntimeError("GEMINI_UNAVAILABLE")

    monkeypatch.setattr("app.gemini_client.analyze_and_runbook", fail)
    monkeypatch.setenv("ALLOW_DOCKER_EXEC", "false")

    r = client.post(
        "/analyze",
        json={
            "incident_description": "Outage",
            "include_logs": False,
            "include_metrics_hint": "",
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert "[Fallback]" in data["analysis"]

    sanitized = "\n".join(data["preview"]["sanitized_lines"])
    approved_hash = data["approved_hash"]
    r2 = client.post(
        "/execute",
        json={"content": sanitized, "content_hash": approved_hash},
    )
    assert r2.status_code == 200
    body = r2.json()
    combined = "\n".join(body["output"])
    assert "execution disabled" in combined.lower() or "disabled" in combined.lower()
