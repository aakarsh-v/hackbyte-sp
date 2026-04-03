# DevOps AI Platform

AI-augmented DevOps demo: three instrumented services, Prometheus + Grafana, FastAPI backend with Gemini (Google AI Studio) for incident analysis and runbook drafts, policy filtering (VeriGuard-style), and safe `docker` execution.

## Prerequisites

- Docker Engine + Docker Compose v2
- Google AI Studio API key for Gemini (optional; fallback templates run without it)

## Quick start (local)

1. Copy env: `cp .env.example .env` and set `GEMINI_API_KEY`.
2. Build the web UI (required for backend image): `cd web && npm ci && npm run build`.
3. From repo root: `docker compose -f infra/docker-compose.yml --env-file .env up --build`.
4. Open:
   - **Console UI:** http://localhost:8000/ (served by backend)
   - Grafana: http://localhost:3000/ (admin / admin)
   - Prometheus: http://localhost:9090/
   - Mini frontend app: http://localhost:3001/
   - Auth API: http://localhost:8081/health
   - Payment API: http://localhost:8082/health

## Cloud VM (e.g. AWS EC2)

1. **Instance:** Ubuntu 22.04+, open inbound **22** (SSH), **8000** (API/UI), **3000** (Grafana, optional), **9090** (Prometheus, optional), **8081–8082**, **3001** as needed. Restrict sources to your IP for the demo.
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
- `POST /analyze` — `{ "incident_description", "include_logs", "include_metrics_hint" }`
- `POST /execute` — `{ "content", "content_hash" }` matching last sanitized runbook from `/analyze`

## Development (UI with Vite proxy)

```bash
cd web && npm ci && npm run dev
```

Opens Vite on port 5173 with proxy to FastAPI on 8000. Run the stack without rebuilding the backend image, or run only `backend` + services via Compose.

## Project layout

- `services/` — auth, payment, frontend microservices (Node.js + Prometheus metrics)
- `backend/` — FastAPI, Gemini, policy, executor
- `infra/` — `docker-compose.yml`, Prometheus, Grafana provisioning
- `web/` — React console (Vite)
