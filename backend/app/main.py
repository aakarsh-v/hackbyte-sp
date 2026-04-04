from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from prometheus_client import Counter, generate_latest, CONTENT_TYPE_LATEST
from pydantic import BaseModel
from starlette.responses import Response

from . import gemini_client
from .cloudwatch_poller import CloudWatchPoller
from .executor import execute_lines
from .models import (
    AnalyzeRequest,
    ApproveRequest,
    ExecuteResponse,
    GeminiAnalysisResponse,
    IncidentQueryRequest,
    IncidentQueryResponse,
    LogEvent,
    LogIngestBatch,
    PolicyPreviewResponse,
)
from .persistence import (
    append_log_event,
    fetch_log_tail,
    fetch_recent_runbook_summaries,
    get_session_runbook,
    set_http_client,
    append_session_runbook,
)
from .policy import hash_content, parse_executable_lines, preview_policy
from .prometheus_snapshot import build_metrics_snapshot
from .session_id import normalize_session_id

LOG_BUFFER_MAX = int(os.environ.get("LOG_BUFFER_MAX", "2000"))

# ---------------------------------------------------------------------------
# Prometheus metrics for the backend itself
# ---------------------------------------------------------------------------
LOGS_INGESTED = Counter(
    "devopsai_logs_ingested_total",
    "Total log events ingested",
    ["service", "level"],
)
ANALYZE_REQUESTS = Counter(
    "devopsai_analyze_requests_total",
    "Total /analyze requests",
    ["result"],  # "ok" | "fallback"
)
EXECUTE_REQUESTS = Counter(
    "devopsai_execute_requests_total",
    "Total /execute requests",
    ["result"],  # "ok" | "blocked" | "error"
)
APPROVE_REQUESTS = Counter(
    "devopsai_approve_requests_total",
    "Total /approve requests",
    ["result"],  # "ok" | "rejected"
)
INCIDENT_QUERY = Counter(
    "devopsai_incident_query_total",
    "Total /incident-query requests",
    ["result"],  # "ok" | "fallback"
)


class AppState:
    def __init__(self) -> None:
        self.ws_clients: list[WebSocket] = []
        self.metrics_events: int = 0
        self.cw_poller: CloudWatchPoller | None = None
        self.log_buffer: "collections.deque" = __import__("collections").deque(maxlen=2000)


state = AppState()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    base = os.environ.get("SPACETIME_HTTP_URL", "http://localhost:3000").rstrip("/")
    timeout = float(os.environ.get("SPACETIME_HTTP_TIMEOUT", "60"))
    headers: dict[str, str] = {}
    bearer = os.environ.get("SPACETIME_BEARER_TOKEN", "").strip()
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    client = httpx.AsyncClient(base_url=base, timeout=timeout, headers=headers)
    set_http_client(client)

    # Start CloudWatch Logs poller if CW_LOG_GROUP env is configured
    async def _cw_on_event(
        service: str, level: str, time: str, message: str, extra: dict
    ) -> None:
        from .models import LogEvent
        event = LogEvent(time=time, service=service, level=level, message=message, extra=extra)
        await append_log_event(event)
        state.metrics_events += 1
        LOGS_INGESTED.labels(service=service, level=level).inc()
        await broadcast_log(event)

    poller = CloudWatchPoller(on_event=_cw_on_event)
    state.cw_poller = poller
    poller.start()

    yield

    poller.stop()
    set_http_client(None)
    await client.aclose()


app = FastAPI(title="DevOps AI Platform API", version="0.2.0", lifespan=lifespan)

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


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------

async def verify_ingest_secret(
    x_ingest_secret: str | None = Header(default=None, alias="X-Ingest-Secret"),
) -> None:
    expected = os.environ.get("INGEST_SECRET", "").strip()
    if not expected:
        return
    if x_ingest_secret != expected:
        raise HTTPException(status_code=401, detail="invalid or missing ingest secret")


@app.post("/ingest")
async def ingest_one(
    event: LogEvent,
    _auth: None = Depends(verify_ingest_secret),
) -> dict[str, str]:
    state.log_buffer.append(event)
    try:
        await append_log_event(event)
    except Exception as exc:
        print(f"[persistence] SpacetimeDB write skipped: {exc}")
    state.metrics_events += 1
    LOGS_INGESTED.labels(service=event.service, level=event.level).inc()
    await broadcast_log(event)
    return {"status": "ok"}


@app.post("/ingest/batch")
async def ingest_batch(
    batch: LogIngestBatch,
    _auth: None = Depends(verify_ingest_secret),
) -> dict[str, Any]:
    for e in batch.events:
        try:
            await append_log_event(e)
        except Exception as exc:
            print(f"[persistence] SpacetimeDB write skipped: {exc}")
        state.metrics_events += 1
        LOGS_INGESTED.labels(service=e.service, level=e.level).inc()
        await broadcast_log(e)
    return {"status": "ok", "count": len(batch.events)}


# ---------------------------------------------------------------------------
# Logs
# ---------------------------------------------------------------------------

@app.get("/logs")
async def get_logs(limit: int = 500) -> dict[str, Any]:
    try:
        items = await fetch_log_tail(min(limit, LOG_BUFFER_MAX))
    except Exception as exc:
        print(f"[persistence] /logs SpacetimeDB read failed, using memory: {exc}")
        items = list(state.log_buffer)[-min(limit, LOG_BUFFER_MAX):]
    return {"events": [e.model_dump() for e in items]}


@app.websocket("/ws/logs")
async def ws_logs(ws: WebSocket) -> None:
    await ws.accept()
    state.ws_clients.append(ws)
    try:
        try:
            events = await fetch_log_tail(200)
        except Exception:
            events = list(state.log_buffer)[-200:]
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


# ---------------------------------------------------------------------------
# Prometheus metrics endpoint for the backend itself
# ---------------------------------------------------------------------------

@app.get("/metrics")
async def metrics() -> Response:
    data = generate_latest()
    return Response(content=data, media_type=CONTENT_TYPE_LATEST)


# ---------------------------------------------------------------------------
# Natural language incident query (logs + optional runbook hints)
# ---------------------------------------------------------------------------

@app.post("/incident-query", response_model=IncidentQueryResponse)
async def incident_query(req: IncidentQueryRequest) -> IncidentQueryResponse:
    q = (req.question or "").strip()
    if not q:
        raise HTTPException(status_code=400, detail="question is required")
    lim = min(max(req.log_limit, 1), LOG_BUFFER_MAX)
    try:
        lines = await fetch_log_tail(lim)
    except Exception:
        lines = list(state.log_buffer)[-lim:]
    log_excerpt = "\n".join(
        f"{e.time} {e.service} [{e.level}] {e.message}" for e in lines
    )
    max_chars = int(os.environ.get("INCIDENT_QUERY_MAX_LOG_CHARS", "120000"))
    if len(log_excerpt) > max_chars:
        log_excerpt = log_excerpt[-max_chars:]

    runbook_excerpt = ""
    if req.include_runbook_hints:
        try:
            runbook_excerpt = await fetch_recent_runbook_summaries(20)
        except Exception as exc:
            print(f"[incident-query] runbook hints skipped: {exc}")

    try:
        answer = await gemini_client.answer_incident_question(
            question=q,
            log_excerpt=log_excerpt,
            runbook_excerpt=runbook_excerpt,
        )
    except Exception as exc:
        print(f"[incident-query] Gemini error: {exc}")
        answer = gemini_client.incident_query_fallback(q, log_excerpt, runbook_excerpt)
        INCIDENT_QUERY.labels(result="fallback").inc()
    else:
        if os.environ.get("GEMINI_API_KEY", "").strip():
            INCIDENT_QUERY.labels(result="ok").inc()
        else:
            INCIDENT_QUERY.labels(result="fallback").inc()
    return IncidentQueryResponse(answer=answer)


# ---------------------------------------------------------------------------
# Analyze
# ---------------------------------------------------------------------------

@app.post("/analyze", response_model=GeminiAnalysisResponse)
async def analyze(
    req: AnalyzeRequest,
    x_session_id: str | None = Header(default=None, alias="X-Session-Id"),
) -> GeminiAnalysisResponse:
    log_excerpt = ""
    if req.include_logs:
        try:
            lines = await fetch_log_tail(400)
        except Exception:
            lines = list(state.log_buffer)[-400:]
        log_excerpt = "\n".join(f"{e.service} [{e.level}] {e.message}" for e in lines)
    metrics_hint = req.include_metrics_hint or ""
    if req.include_prometheus_snapshot:
        prom_base = os.environ.get("PROMETHEUS_URL", "http://prometheus:9090").strip()
        snap = await build_metrics_snapshot(prom_base)
        metrics_hint = (
            f"{metrics_hint}\n\n{snap}".strip() if metrics_hint else snap
        )
    try:
        analysis, raw_runbook, preview, h = await gemini_client.analyze_and_runbook(
            incident_description=req.incident_description,
            log_excerpt=log_excerpt,
            metrics_hint=metrics_hint,
            image_base64=req.image_base64,
            image_mime_type=req.image_mime_type,
        )
        ANALYZE_REQUESTS.labels(result="ok").inc()
    except Exception:
        analysis, raw_runbook, preview, h = gemini_client.fallback_template(
            req.incident_description, log_excerpt
        )
        ANALYZE_REQUESTS.labels(result="fallback").inc()
    sanitized = "\n".join(preview.sanitized_lines)
    sid = normalize_session_id(x_session_id)
    try:
        await append_session_runbook(
            session_id=sid,
            last_sanitized=sanitized,
            last_sanitized_hash=h,
        )
    except Exception as exc:
        print(f"[persistence] append_session_runbook skipped: {exc}")
    return GeminiAnalysisResponse(
        analysis=analysis,
        raw_runbook=raw_runbook,
        preview=preview,
        approved_hash=h,
    )


# ---------------------------------------------------------------------------
# Policy preview (for re-hash when user edits the runbook)
# ---------------------------------------------------------------------------

class PreviewBody(BaseModel):
    script: str


@app.post("/policy/preview", response_model=PolicyPreviewResponse)
async def policy_preview(body: PreviewBody) -> PolicyPreviewResponse:
    return preview_policy(body.script)


# ---------------------------------------------------------------------------
# Approve — mandatory gate before execute
# ---------------------------------------------------------------------------

@app.post("/approve")
async def approve(
    req: ApproveRequest,
    x_session_id: str | None = Header(default=None, alias="X-Session-Id"),
) -> dict[str, Any]:
    sid = normalize_session_id(x_session_id)
    try:
        last_sanitized, last_hash = await get_session_runbook(sid)
    except Exception:
        last_sanitized, last_hash = None, None
    h = hash_content(req.content)
    if h != req.content_hash:
        APPROVE_REQUESTS.labels(result="rejected").inc()
        raise HTTPException(400, "content_hash does not match submitted content")
    # If there's a stored runbook, the submitted content must match it
    if last_sanitized and req.content.strip() != last_sanitized.strip():
        APPROVE_REQUESTS.labels(result="rejected").inc()
        raise HTTPException(400, "content must match last sanitized runbook from /analyze")
    # Update stored runbook with exactly what operator approved (may have been edited)
    try:
        await append_session_runbook(
            session_id=sid,
            last_sanitized=req.content,
            last_sanitized_hash=h,
        )
    except Exception as exc:
        print(f"[persistence] append_session_runbook (approve) skipped: {exc}")
    APPROVE_REQUESTS.labels(result="ok").inc()
    return {"status": "approved", "hash": h}


# ---------------------------------------------------------------------------
# Execute — requires prior /approve (checks stored approved content + hash)
# ---------------------------------------------------------------------------

@app.post("/execute", response_model=ExecuteResponse)
async def execute(
    req: ApproveRequest,
    x_session_id: str | None = Header(default=None, alias="X-Session-Id"),
) -> ExecuteResponse:
    sid = normalize_session_id(x_session_id)
    try:
        last_sanitized, last_hash = await get_session_runbook(sid)
    except Exception:
        last_sanitized, last_hash = req.content, req.content_hash
    h = hash_content(req.content)
    if not last_hash or h != req.content_hash:
        EXECUTE_REQUESTS.labels(result="error").inc()
        raise HTTPException(400, "invalid or missing approval hash — call /approve first")
    if req.content.strip() != (last_sanitized or "").strip():
        EXECUTE_REQUESTS.labels(result="error").inc()
        raise HTTPException(400, "content must match last approved runbook")

    allow = os.environ.get("ALLOW_DOCKER_EXEC", "true").lower() in ("1", "true", "yes")
    lines = parse_executable_lines(req.content)
    out: list[str] = []
    async for chunk in execute_lines(lines, allow_docker=allow):
        out.append(chunk)

    err = None
    if any("blocked" in o.lower() for o in out):
        err = "some lines were blocked at JIT — see output"
        EXECUTE_REQUESTS.labels(result="blocked").inc()
    else:
        EXECUTE_REQUESTS.labels(result="ok").inc()
    return ExecuteResponse(ok=err is None, steps_run=lines, output=out, error=err)


# ---------------------------------------------------------------------------
# Execute/stream — SSE endpoint, yields output lines as they arrive
# ---------------------------------------------------------------------------

@app.post("/execute/stream")
async def execute_stream(
    req: ApproveRequest,
    x_session_id: str | None = Header(default=None, alias="X-Session-Id"),
) -> StreamingResponse:
    sid = normalize_session_id(x_session_id)
    try:
        last_sanitized, last_hash = await get_session_runbook(sid)
    except Exception:
        last_sanitized, last_hash = req.content, req.content_hash
    h = hash_content(req.content)
    if not last_hash or h != req.content_hash:
        raise HTTPException(400, "invalid or missing approval hash — call /approve first")
    if req.content.strip() != (last_sanitized or "").strip():
        raise HTTPException(400, "content must match last approved runbook")

    allow = os.environ.get("ALLOW_DOCKER_EXEC", "true").lower() in ("1", "true", "yes")
    lines = parse_executable_lines(req.content)

    async def event_generator() -> AsyncIterator[str]:
        try:
            async for chunk in execute_lines(lines, allow_docker=allow):
                payload = json.dumps({"type": "output", "data": chunk})
                yield f"data: {payload}\n\n"
                await asyncio.sleep(0)  # let event loop breathe
            yield f"data: {json.dumps({'type': 'done'})}\n\n"
        except Exception as exc:
            yield f"data: {json.dumps({'type': 'error', 'data': str(exc)})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Static UI: Docker uses /app/app + ../web/dist; local dev uses backend/app + ../../web/dist
# ---------------------------------------------------------------------------
_here = os.path.dirname(os.path.abspath(__file__))
for _rel in (("..", "web", "dist"), ("..", "..", "web", "dist")):
    _web_dist = os.path.abspath(os.path.join(_here, *_rel))
    if os.path.isdir(_web_dist):
        app.mount("/", StaticFiles(directory=_web_dist, html=True), name="ui")
        break
