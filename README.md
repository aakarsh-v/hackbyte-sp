# DevOps AI Platform

AI-augmented DevOps demo: three instrumented services, Prometheus + Grafana, FastAPI backend with Gemini (Google AI Studio) for incident analysis and runbook drafts, policy filtering (VeriGuard-style), and safe `docker` execution.

## Prerequisites

- Docker Engine + Docker Compose v2
- Google AI Studio API key for Gemini (optional; fallback templates run without it)

The stack includes **SpacetimeDB** (standalone in Docker) for persisted log events and **per-session** runbook state (sanitized script + hash). The Rust module in [`spacetimedb/devops-module/`](spacetimedb/devops-module/) defines public tables `log_event` and `session_runbook`, plus reducers `ingest_log` and `upsert_session_runbook`. The FastAPI app talks to SpacetimeDB over its **HTTP API** (`/v1/database/.../call/...` and `/sql`). The web UI sends a stable **`X-Session-Id`** (stored in `localStorage`) on analyze/execute so concurrent operators do not overwrite each other’s runbooks. API clients may omit the header to use the default session id `00000000-0000-0000-0000-000000000001`.

**SpacetimeDB (details):** see [docs/SPACETIMEDB.md](docs/SPACETIMEDB.md) for architecture, environment variables, Compose startup, module schema, backend HTTP mapping, and troubleshooting.

## Quick start (local)

1. Copy env: `cp .env.example .env` and set `GEMINI_API_KEY`. For Compose, defaults assume **`SPACETIME_HTTP_URL=http://spacetime:3000`** inside the stack (set in `infra/docker-compose.yml`). For tools on the host talking to the published SpacetimeDB port, use **`SPACETIME_HTTP_URL=http://localhost:3004`** (see compose port mapping).
2. Build the web UI (required for backend image): `cd web && npm ci && npm run build`.
3. From repo root: `docker compose -f infra/docker-compose.yml --env-file .env up --build`. The **`st-init`** one-shot service runs `spacetime publish` against the module under `spacetimedb/devops-module` (first run may take several minutes while Rust/WASM builds). **`backend`** starts only after **`st-init`** completes successfully.
4. Open:
   - **Console UI:** http://localhost:8000/ (served by Uvicorn)
   - **SpacetimeDB** (host): http://localhost:3004/v1/ping — optional connectivity check
   - Grafana: http://localhost:3000/ (admin / admin)
   - Prometheus: http://localhost:9090/
   - Mini frontend app: http://localhost:3001/
   - Auth API: http://localhost:8081/health
   - Payment API: http://localhost:8082/health

## Cloud VM (e.g. AWS EC2)

1. **Instance:** Ubuntu 22.04+, open inbound **22** (SSH), **8000** (API/UI), **3000** (Grafana, optional), **3004** (SpacetimeDB HTTP, optional), **9090** (Prometheus, optional), **8081–8082**, **3001** as needed. Restrict sources to your IP for the demo.
2. Install Docker: follow [Docker Engine install for Ubuntu](https://docs.docker.com/engine/install/ubuntu/).
3. Clone this repo on the VM, add `.env` with `GEMINI_API_KEY`.
4. Build web: `cd web && npm ci && npm run build` (install Node 20 via nvm or distro).
5. Run: `docker compose -f infra/docker-compose.yml --env-file .env up -d --build`.
6. Browse `http://<PUBLIC_IP>:8000/` for the console.

**Security:** Do not expose the Docker socket publicly. The backend container mounts `/var/run/docker.sock` only on the trusted host.

## Demo flow

1. Generate traffic: open http://localhost:3001/ and use Login / Pay, or `curl` the health endpoints.
2. (Optional) Induce failure: see [scripts/fault-inject.sh](scripts/fault-inject.sh) or set `FAIL_MODE=1` for `payment-service` in [infra/docker-compose.yml](infra/docker-compose.yml) and recreate that service.
3. Open the console at http://localhost:8000/, enter an incident description, click **Analyze + runbook**.
4. Review **blocked** lines and the **sanitized** script; click **Execute approved runbook** (runs allowed `docker` / `echo` / `sleep` lines only).

## API (curl)

- `POST /ingest` — JSON body: `{ "service", "level", "message", "time"? }`
- `POST /analyze` — body `{ "incident_description", "include_logs", "include_metrics_hint" }`; optional header **`X-Session-Id`** (UUID) scopes stored runbook state
- `POST /execute` — `{ "content", "content_hash" }` matching last sanitized runbook for that session; same **`X-Session-Id`** as analyze

## Development (UI with Vite proxy)

```bash
cd web && npm ci && npm run dev
```

Opens Vite on port 5173 with proxy to FastAPI on 8000. Run the stack without rebuilding the backend image, or run only `backend` + services via Compose.

**Backend without Docker:** run a local SpacetimeDB (`spacetime start` from the [CLI](https://spacetimedb.com/docs)), publish the module (`cd spacetimedb/devops-module && spacetime publish devopsai`), set `SPACETIME_HTTP_URL=http://127.0.0.1:3000` and `SPACETIME_DATABASE=devopsai`, then from `backend/`: `pip install -r requirements.txt` and `uvicorn app.main:app --reload --host 0.0.0.0 --port 8000` (with `web` built or `VITE_API_URL` pointing at this API).

## Project layout

- `spacetimedb/devops-module/` — Rust SpacetimeDB module (WASM)
- `services/` — auth, payment, frontend microservices (Node.js + Prometheus metrics)
- `backend/` — FastAPI, Gemini, policy, executor, SpacetimeDB HTTP client
- `infra/` — `docker-compose.yml`, Prometheus, Grafana provisioning
- `web/` — React console (Vite)

## Documentation

- **SpacetimeDB:** [docs/SPACETIMEDB.md](docs/SPACETIMEDB.md) — integration with this repo, configuration, and operations.

For a detailed, file-grounded list of **what is implemented** and **planned / future work**, you can maintain **`IMPLEMENTATION_STATUS.md`** at the repo root locally. Root-level Markdown files other than `README.md` are gitignored by default (see `.gitignore`); use `git add -f` only if you intentionally want to commit extra docs.
