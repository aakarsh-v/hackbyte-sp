from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class LogEvent(BaseModel):
    """Structured log line (PDF schema)."""

    time: str = Field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    service: str
    level: str = "INFO"
    message: str
    extra: dict[str, Any] | None = None


class LogIngestBatch(BaseModel):
    events: list[LogEvent]


class AnalyzeRequest(BaseModel):
    """Trigger RCA + runbook generation."""

    incident_description: str = ""
    """Free-text incident (e.g. user prompt)."""

    include_logs: bool = True
    """Attach recent ring-buffer logs to the prompt."""

    include_metrics_hint: str = ""
    """Optional pasted metric summary or Prometheus query result text."""

    include_prometheus_snapshot: bool = False
    """When True, backend appends live instant-query results from PROMETHEUS_URL."""

    image_base64: str = ""
    """Optional base64-encoded image (no data: URL prefix). Sent to Gemini as context."""

    image_mime_type: str = "image/png"
    """MIME type for the image (e.g. image/png, image/jpeg, image/webp)."""


class PolicyViolation(BaseModel):
    line_number: int
    line: str
    reason: str


class PolicyPreviewResponse(BaseModel):
    original_lines: list[str]
    sanitized_lines: list[str]
    blocked: list[PolicyViolation]


class ApproveRequest(BaseModel):
    """Approve sanitized runbook for execution (versioned by hash)."""

    content: str
    """Full script text after user reviewed policy preview."""

    content_hash: str
    """SHA256 hex of approved content; must match server-computed hash of last preview."""


class ExecuteResponse(BaseModel):
    ok: bool
    steps_run: list[str]
    output: list[str]
    error: str | None = None


class GeminiAnalysisResponse(BaseModel):
    analysis: str
    raw_runbook: str
    preview: PolicyPreviewResponse
    approved_hash: str | None = None
