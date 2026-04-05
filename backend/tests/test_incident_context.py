"""Tests for token-efficient incident context compression and heuristic."""

from app.gemini_client import (
    compress_log_lines_for_prompt,
    heuristic_incident_context,
)
from app.models import LogEvent


def test_compress_truncates_message_and_formats_pipe():
    events = [
        LogEvent(
            time="2026-01-01T00:00:00Z",
            service="pay",
            level="ERROR",
            message="x" * 200,
        )
    ]
    out = compress_log_lines_for_prompt(events, max_lines=10, max_msg_len=20)
    assert "pay" in out
    assert "ERROR" in out
    assert "…" in out or len(out.split("|")[-1]) <= 21


def test_compress_empty_events():
    assert compress_log_lines_for_prompt([], max_lines=10, max_msg_len=100) == ""


def test_heuristic_empty_compressed():
    text = heuristic_incident_context("")
    assert "No log lines" in text or "empty" in text.lower()


def test_heuristic_counts_services_and_errors():
    compressed = "\n".join(
        [
            "t1|auth-service|INFO|ok",
            "t2|payment-service|ERROR|timeout on /pay",
            "t3|payment-service|WARN|slow",
        ]
    )
    text = heuristic_incident_context(compressed)
    assert "auth-service" in text or "payment-service" in text
    assert "error" in text.lower()
