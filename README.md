# DevOps AI Platform

AI-augmented DevOps demo: three instrumented services, Prometheus + Grafana, FastAPI backend with Gemini (Google AI Studio) for incident analysis and runbook drafts, policy filtering (VeriGuard-style), and safe `docker` execution.

The stack includes **SpacetimeDB** (standalone in Docker) for persisted log events and **per-session** runbook state (sanitized script + hash). The Rust module in [`spacetimedb/devops-module/`](spacetimedb/devops-module/) defines public tables `log_event` and `session_runbook`, plus reducers `ingest_log` and `upsert_session_runbook`. The FastAPI app talks to SpacetimeDB over its **HTTP API** (`/v1/database/.../call/...` and `/sql`). The web UI sends a stable **`X-Session-Id`** (stored in `localStorage`) on analyze/execute so concurrent operators do not overwrite each other’s runbooks. API clients may omit the header to use the default session id `00000000-0000-0000-0000-000000000001`.

**SpacetimeDB (architecture and troubleshooting):** [docs/SPACETIMEDB.md](docs/SPACETIMEDB.md)

---

## Prerequisites

| Tool | Purpose |
|------|---------|
| **Docker Engine** + **Docker Compose v2** | Run the full stack |
| **Node.js 20+** and **npm** | Build the React console (`web/`) |
| **Python 3.12+** with `pip` | Backend unit tests (optional, local) |
| **bash**, **curl** | E2E script `scripts/verify-stack.sh` (Git Bash or WSL on Windows) |

- **Google AI Studio API key** for Gemini — optional; the backend uses fallback templates when it is not set.

---

## Run the application (step by step)

Do this from the **repository root** unless a path is given.

### 1. Environment file

Copy the example env and edit values as needed:

```bash
cp .env.example .env
```

- Set **`GEMINI_API_KEY`** in `.env` if you want live Gemini responses ([Google AI Studio](https://aistudio.google.com/apikey)).
- **`SPACETIME_DATABASE`** must match the name used when publishing the module (default `devopsai`).
- With **Docker Compose**, the backend container already uses **`SPACETIME_HTTP_URL=http://spacetime:3000`** on the internal network (see `infra/docker-compose.yml`). You normally **do not** set `SPACETIME_HTTP_URL` in `.env` for Compose.
- For tools on the **host** talking to SpacetimeDB’s published port, use **`http://localhost:3004`** (Compose maps host `3004` → container `3000`).

### 2. Build the web console

The backend Docker image **embeds** `web/dist`, so you must build the UI **before** building or starting the stack:

```bash
npm run build:web
```

Equivalent manual commands:

```bash
cd web && npm ci && npm run build && cd ..
```

If `npm run build:web` fails, install Node 20+ and retry.

### 3. Start the stack

**Embedded local SpacetimeDB** (default `npm` scripts — publishes the Rust module inside Docker):

**Foreground** (logs in the terminal; Ctrl+C stops the stack):

```bash
npm run stack:up
```

**Detached** (containers run in the background):

```bash
npm run stack:up:detached
```

Equivalent manual command:

```bash
docker compose --profile local-spacetime -f infra/docker-compose.yml --env-file .env up --build
```

Add `-d` for detached mode.

**SpacetimeDB Maincloud** instead of local containers: set `SPACETIME_HTTP_URL` and `SPACETIME_BEARER_TOKEN` in `.env` (see [docs/SPACETIMEDB.md](docs/SPACETIMEDB.md)), then start **without** the `local-spacetime` profile:

```bash
npm run stack:up:maincloud
```

### 4. What to expect on first start

**If using embedded local SpacetimeDB** (`--profile local-spacetime`):

1. **SpacetimeDB** starts and passes its healthcheck.
2. **`st-init`** runs once: `spacetime publish` for `spacetimedb/devops-module`. The **first** run can take **several minutes** (Rust → WASM compile). Later starts are faster if build caches exist.
3. **`backend`** starts after **`st-init`** completes (when those services are enabled).

**If using Maincloud**, there is no local `spacetime` / `st-init`; the backend connects to `https://maincloud.spacetimedb.com` — publish the module to Maincloud first.

4. Other services (auth, payment, frontend, Prometheus, Grafana) come up per `infra/docker-compose.yml`.

If the backend seems slow to listen, wait; the E2E script can wait up to **600s** by default (override with `WAIT_SECS`).

### 5. URLs after services are healthy

| Service | URL | Notes |
|---------|-----|--------|
| **Console UI** (React, served by backend) | http://localhost:8000/ | Main demo |
| **Backend health** | http://localhost:8000/health | JSON health |
| **SpacetimeDB** (host) | http://localhost:3004/v1/ping | Should return 200 |
| **Grafana** | http://localhost:3000/ | admin / admin |
| **Prometheus** | http://localhost:9090/ | Metrics UI |
| **Mini frontend app** | http://localhost:3001/ | Generates traffic |
| **Auth API** | http://localhost:8081/health | |
| **Payment API** | http://localhost:8082/health | |

---

## Verify that everything works

### A. Quick HTTP checks (host)

Run these in a second terminal while the stack is up:

```bash
curl -sf http://localhost:8000/health
curl -sf http://localhost:3004/v1/ping
curl -sf "http://localhost:9090/api/v1/query?query=up"
curl -sf http://localhost:8081/health
curl -sf http://localhost:8082/health
```

You should get **HTTP 200** responses (Prometheus returns JSON with `"status":"success"` for the query).

### B. Manual UI check

1. Open **http://localhost:8000/**.
2. Optionally open **http://localhost:3001/** and use Login / Pay to generate traffic (or use the `curl` health calls above).
3. In the console, enter an incident description and click **Analyze + runbook**.
4. Confirm **blocked** unsafe lines and a **sanitized** script; click **Execute approved runbook** (only allowed `docker` / `echo` / `sleep` lines run).

Optional failure demo: [scripts/fault-inject.sh](scripts/fault-inject.sh) or set `FAIL_MODE=1` for `payment-service` in [infra/docker-compose.yml](infra/docker-compose.yml) and recreate that service.

### C. Automated tests

**1) Backend unit tests (no Docker)**

Install dev dependencies once:

```bash
cd backend
pip install -r requirements.txt -r requirements-dev.txt
cd ..
```

From the **repo root**:

```bash
npm run test:unit
```

Equivalent:

```bash
cd backend && python -m pytest tests -v && cd ..
```

**2) Full-stack E2E script (requires running Compose)**

With the stack up (detached or in another terminal), from the **repo root**:

```bash
npm run test:e2e
```

Equivalent:

```bash
bash scripts/verify-stack.sh
```

The script waits for **`/health`** (default **600** seconds). If the first Spacetime publish is still compiling, increase the wait:

```bash
WAIT_SECS=1200 npm run test:e2e
```

Override endpoints if your ports differ: **`BASE_URL`**, **`PROM_URL`**, **`ST_URL`**.

On **Windows**, use **Git Bash** or **WSL** so `bash` and the script path work.

### D. Stop the stack

```bash
npm run stack:down
```

Or:

```bash
docker compose -f infra/docker-compose.yml --env-file .env down
```

Add **`-v`** to remove volumes if you need a clean SpacetimeDB state (destructive).

---

## Cloud VM (e.g. AWS EC2)

1. **Instance:** Ubuntu 22.04+, open inbound **22** (SSH), **8000** (API/UI), **3000** (Grafana, optional), **3004** (SpacetimeDB HTTP, optional), **9090** (Prometheus, optional), **8081–8082**, **3001** as needed. Restrict sources to your IP for the demo.
2. Install Docker: [Docker Engine install for Ubuntu](https://docs.docker.com/engine/install/ubuntu/).
3. Clone the repo, copy `.env.example` to `.env`, set **`GEMINI_API_KEY`** if desired.
4. Install Node 20 (e.g. nvm) and run **`npm run build:web`** from the repo root.
5. Run **`npm run stack:up:detached`** (or the same `docker compose` command with `-d`).
6. Open `http://<PUBLIC_IP>:8000/` for the console.

**Security:** Do not expose the Docker socket publicly. The backend container mounts `/var/run/docker.sock` only on the trusted host.

---

## Development (Vite dev server)

```bash
cd web && npm ci && npm run dev
```

Opens **http://localhost:5173** with a proxy to FastAPI on **8000**. Run the Compose stack (or at least backend + dependencies) so API calls succeed. Ensure **`CORS_ORIGINS`** in `.env` / Compose includes `http://localhost:5173` (see `.env.example`).

**Backend without Docker:** run a local SpacetimeDB (`spacetime start` from the [CLI](https://spacetimedb.com/docs)), publish the module (`cd spacetimedb/devops-module && spacetime publish devopsai`), set `SPACETIME_HTTP_URL=http://127.0.0.1:3000` and `SPACETIME_DATABASE=devopsai`, then from `backend/`: `pip install -r requirements.txt` and `uvicorn app.main:app --reload --host 0.0.0.0 --port 8000` (with `web` built or `VITE_API_URL` pointing at this API).

---

## API (curl)

- `POST /ingest` — JSON: `{ "service", "level", "message", "time"? }`
- `POST /analyze` — `{ "incident_description", "include_logs", "include_metrics_hint" }`; optional header **`X-Session-Id`** (UUID)
- `POST /execute` — `{ "content", "content_hash" }` matching the last sanitized runbook for that session; same **`X-Session-Id`** as analyze

---

## Project layout

- `spacetimedb/devops-module/` — Rust SpacetimeDB module (WASM)
- `services/` — auth, payment, frontend microservices (Node.js + Prometheus metrics)
- `backend/` — FastAPI, Gemini, policy, executor, SpacetimeDB HTTP client
- `infra/` — `docker-compose.yml`, Prometheus, Grafana provisioning
- `web/` — React console (Vite)
- `scripts/` — `verify-stack.sh` (full-stack check), `fault-inject.sh`
- `package.json` (repo root) — **`npm run build:web`**, **`stack:up`**, **`test:unit`**, **`test:e2e`**

---

## Documentation

- **SpacetimeDB:** [docs/SPACETIMEDB.md](docs/SPACETIMEDB.md)

For a detailed list of what is implemented versus planned work, you can maintain **`IMPLEMENTATION_STATUS.md`** at the repo root locally (gitignored by default; see `.gitignore`).

---

## CI

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs on push/PR to `main` / `master`:

- Builds **`web/`**, runs **pytest** in **`backend/`**, runs a small **policy import** smoke check, **builds** Compose images, and in a separate job starts the **full Compose stack** and runs **`scripts/verify-stack.sh`** (with `WAIT_SECS=1200`).

Optional: add a repository secret **`GEMINI_API_KEY`** so CI’s running backend can call Gemini during the E2E job.
