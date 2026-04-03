from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
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
from .persistence import (
    append_log_event,
    fetch_log_tail,
    get_session_runbook,
    set_http_client,
    upsert_session_runbook,
)
from .policy import hash_content, parse_executable_lines, preview_policy
from .session_id import normalize_session_id

LOG_BUFFER_MAX = int(os.environ.get("LOG_BUFFER_MAX", "2000"))


class AppState:
    def __init__(self) -> None:
        self.ws_clients: list[WebSocket] = []
        self.metrics_events: int = 0


state = AppState()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    base = os.environ.get("SPACETIME_HTTP_URL", "http://localhost:3000").rstrip("/")
    timeout = float(os.environ.get("SPACETIME_HTTP_TIMEOUT", "60"))
    client = httpx.AsyncClient(base_url=base, timeout=timeout)
    set_http_client(client)
    yield
    set_http_client(None)
    await client.aclose()


app = FastAPI(title="DevOps AI Platform API", version="0.1.0", lifespan=lifespan)

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
    await append_log_event(event)
    state.metrics_events += 1
    await broadcast_log(event)
    return {"status": "ok"}


@app.post("/ingest/batch")
async def ingest_batch(batch: LogIngestBatch) -> dict[str, Any]:
    for e in batch.events:
        await append_log_event(e)
        state.metrics_events += 1
        await broadcast_log(e)
    return {"status": "ok", "count": len(batch.events)}


@app.get("/logs")
async def get_logs(limit: int = 500) -> dict[str, Any]:
    items = await fetch_log_tail(min(limit, LOG_BUFFER_MAX))
    return {"events": [e.model_dump() for e in items]}


@app.websocket("/ws/logs")
async def ws_logs(ws: WebSocket) -> None:
    await ws.accept()
    state.ws_clients.append(ws)
    try:
        events = await fetch_log_tail(200)
        for e in events:
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
async def analyze(
    req: AnalyzeRequest,
    x_session_id: str | None = Header(default=None, alias="X-Session-Id"),
) -> GeminiAnalysisResponse:
    log_excerpt = ""
    if req.include_logs:
        lines = await fetch_log_tail(400)
        log_excerpt = "\n".join(f"{e.service} [{e.level}] {e.message}" for e in lines)
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
    sid = normalize_session_id(x_session_id)
    await upsert_session_runbook(
        session_id=sid,
        last_sanitized=sanitized,
        last_sanitized_hash=h,
    )
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
async def approve(
    req: ApproveRequest,
    x_session_id: str | None = Header(default=None, alias="X-Session-Id"),
) -> dict[str, Any]:
    sid = normalize_session_id(x_session_id)
    last_sanitized, last_hash = await get_session_runbook(sid)
    h = hash_content(req.content)
    if last_hash and h != req.content_hash:
        raise HTTPException(400, "content_hash does not match approved sanitized script")
    if last_sanitized and req.content.strip() != last_sanitized.strip():
        raise HTTPException(400, "content must match last sanitized runbook")
    return {"status": "approved", "hash": h}


@app.post("/execute", response_model=ExecuteResponse)
async def execute(
    req: ApproveRequest,
    x_session_id: str | None = Header(default=None, alias="X-Session-Id"),
) -> ExecuteResponse:
    sid = normalize_session_id(x_session_id)
    last_sanitized, last_hash = await get_session_runbook(sid)
    h = hash_content(req.content)
    if not last_hash or h != req.content_hash:
        raise HTTPException(400, "invalid or missing approval hash")
    if req.content.strip() != (last_sanitized or "").strip():
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
