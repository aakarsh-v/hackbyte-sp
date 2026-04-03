from __future__ import annotations

DEFAULT_SESSION_ID = "00000000-0000-0000-0000-000000000001"


def normalize_session_id(header_value: str | None) -> str:
    s = (header_value or "").strip()
    return s if s else DEFAULT_SESSION_ID
