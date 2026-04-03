from __future__ import annotations

import os
from collections import deque
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import gemini_client
from .executor import execute_lines
from .models import (
    AnalyzeRequest,
    ApproveRequest,
    ExecuteResponse,
    GeminiAnalysisResponse,
    LogEvent,
    LogIngestBatch,
    PolicyPreviewResponse,
)
from .policy import hash_content, parse_executable_lines, preview_policy
from .policy import jit_check_line

LOG_BUFFER_MAX = int(os.environ.get("LOG_BUFFER_MAX", "2000"))


class AppState:
    def __init__(self) -> None:
        self.log_buffer: deque[LogEvent] = deque(maxlen=LOG_BUFFER_MAX)
        self.ws_clients: list[WebSocket] = []
        self.last_sanitized: str | None = None
        self.last_sanitized_hash: str | None = None
        self.metrics_events: int = 0


state = AppState()

app = FastAPI(title="DevOps AI Platform API", version="0.1.0")

_origins = os.environ.get("CORS_ORIGINS", "http://localhost:5173").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _origins if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


async def broadcast_log(event: LogEvent) -> None:
    dead: list[WebSocket] = []
    payload = event.model_dump_json()
    for ws in state.ws_clients:
        try:
            await ws.send_text(payload)
        except Exception:
            dead.append(ws)
    for ws in dead:
        if ws in state.ws_clients:
            state.ws_clients.remove(ws)


@app.post("/ingest")
async def ingest_one(event: LogEvent) -> dict[str, str]:
    state.log_buffer.append(event)
    state.metrics_events += 1
    await broadcast_log(event)
    return {"status": "ok"}


@app.post("/ingest/batch")
async def ingest_batch(batch: LogIngestBatch) -> dict[str, Any]:
    for e in batch.events:
        state.log_buffer.append(e)
        state.metrics_events += 1
        await broadcast_log(e)
    return {"status": "ok", "count": len(batch.events)}


@app.get("/logs")
async def get_logs(limit: int = 500) -> dict[str, Any]:
    items = list(state.log_buffer)[-limit:]
    return {"events": [e.model_dump() for e in items]}


@app.websocket("/ws/logs")
async def ws_logs(ws: WebSocket) -> None:
    await ws.accept()
    state.ws_clients.append(ws)
    try:
        for e in list(state.log_buffer)[-200:]:
            await ws.send_text(e.model_dump_json())
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                break
    except WebSocketDisconnect:
        pass
    finally:
        if ws in state.ws_clients:
            state.ws_clients.remove(ws)


@app.post("/analyze", response_model=GeminiAnalysisResponse)
async def analyze(req: AnalyzeRequest) -> GeminiAnalysisResponse:
    log_excerpt = ""
    if req.include_logs:
        lines = list(state.log_buffer)[-400:]
        log_excerpt = "\n".join(
            f"{e.service} [{e.level}] {e.message}" for e in lines
        )
    try:
        analysis, raw_runbook, preview, h = await gemini_client.analyze_and_runbook(
            incident_description=req.incident_description,
            log_excerpt=log_excerpt,
            metrics_hint=req.include_metrics_hint,
        )
    except Exception:
        analysis, raw_runbook, preview, h = gemini_client.fallback_template(
            req.incident_description, log_excerpt
        )
    sanitized = "\n".join(preview.sanitized_lines)
    state.last_sanitized = sanitized
    state.last_sanitized_hash = h
    return GeminiAnalysisResponse(
        analysis=analysis,
        raw_runbook=raw_runbook,
        preview=preview,
        approved_hash=h,
    )


class PreviewBody(BaseModel):
    script: str


@app.post("/policy/preview", response_model=PolicyPreviewResponse)
async def policy_preview(body: PreviewBody) -> PolicyPreviewResponse:
    return preview_policy(body.script)


@app.post("/approve")
async def approve(req: ApproveRequest) -> dict[str, Any]:
    h = hash_content(req.content)
    if state.last_sanitized_hash and h != req.content_hash:
        raise HTTPException(400, "content_hash does not match approved sanitized script")
    if state.last_sanitized and req.content.strip() != state.last_sanitized.strip():
        raise HTTPException(400, "content must match last sanitized runbook")
    return {"status": "approved", "hash": h}


@app.post("/execute", response_model=ExecuteResponse)
async def execute(req: ApproveRequest) -> ExecuteResponse:
    h = hash_content(req.content)
    if not state.last_sanitized_hash or h != req.content_hash:
        raise HTTPException(400, "invalid or missing approval hash")
    if req.content.strip() != (state.last_sanitized or "").strip():
        raise HTTPException(400, "content must match last sanitized runbook")

    allow = os.environ.get("ALLOW_DOCKER_EXEC", "true").lower() in ("1", "true", "yes")
    lines = parse_executable_lines(req.content)
    out: list[str] = []
    async for chunk in execute_lines(lines, allow_docker=allow):
        out.append(chunk)

    err = None
    if any("blocked" in o.lower() for o in out):
        err = "some lines were blocked at JIT — see output"
    return ExecuteResponse(ok=err is None, steps_run=lines, output=out, error=err)


# Static UI: Docker uses /app/app + ../web/dist; local dev uses backend/app + ../../web/dist
_here = os.path.dirname(os.path.abspath(__file__))
for _rel in (("..", "web", "dist"), ("..", "..", "web", "dist")):
    _web_dist = os.path.abspath(os.path.join(_here, *_rel))
    if os.path.isdir(_web_dist):
        app.mount("/", StaticFiles(directory=_web_dist, html=True), name="ui")
        break


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
