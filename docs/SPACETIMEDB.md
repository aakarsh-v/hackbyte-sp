# SpacetimeDB integration

This project uses [SpacetimeDB](https://spacetimedb.com/) as the **only** persistence layer for:

1. **`logs`** — append-only log lines ingested from microservices and replayed to the web UI (`GET /logs`, WebSocket `/ws/logs`).
2. **`session_runbook_history`** — **append-only** history of sanitized runbook scripts and hashes per `session_id` (aligned with **`X-Session-Id`**). The backend uses the row with the largest **`id`** as the current runbook for `/approve` and `/execute`.

The FastAPI backend does **not** use the legacy Python SDK on PyPI. It uses **`httpx`** against SpacetimeDB’s **HTTP API** (CLI 2.x): `POST /v1/database/{name}/call/{reducer}` and `POST /v1/database/{name}/sql`.

### Runbook history vs stable `X-Session-Id`

- The web UI stores one UUID in `localStorage` and sends it on every request as **`X-Session-Id`**. Reusing the **same** UUID does **not** by itself replace runbook rows: the table’s primary key is **`id`** (auto-increment), and `append_session_runbook` **inserts** a new row per call. Many rows can share the same `session_id` string.
- If you only see **one row per session** or rows **disappearing**, the SpacetimeDB instance is likely running an **older published module** (where `session_id` was the primary key). **Republish** the current [`spacetimedb/devops-module`](../spacetimedb/devops-module/) so `SPACETIME_DATABASE` matches your publish target. If publish cannot migrate the live schema, use a **new** `SPACETIME_DATABASE` name or a fresh database.
- To **isolate incidents in the DB** under different `session_id` values, use the console’s **New incident session** control (generates a new UUID). The backend does not need changes for this.

### Manual verification (operators)

1. **Append-only with one browser session** — Run **Analyze** twice without changing `X-Session-Id`. Query SpacetimeDB, e.g. `SELECT * FROM session_runbook_history WHERE session_id = '<your-uuid>'` (escape quotes as required). Expect **two rows** with different **`id`** values and the same **`session_id`**.
2. **New session** — Click **New incident session**, run **Analyze** once. A new `session_id` should appear in storage; the new row should use that UUID in **`session_id`**.

### Migrating from legacy schema (dashboard shows `session_id` as primary key)

If the SpacetimeDB **table browser** shows **`session_id` with a key icon** (primary key) on `session_runbook_history` (or an old `session_runbook` table) and only **one row** no matter how often you analyze, the hosted database is still running an **old module** where `session_id` was unique and each write replaced the row.

**Fix (required):** publish the current [`spacetimedb/devops-module`](../spacetimedb/devops-module/) so the table has **`id` (auto-increment) as primary key** and reducer **`append_session_runbook`** (insert-only).

1. From the repo: `cd spacetimedb/devops-module` and run `spacetime publish <DATABASE_NAME> ...` against your server (see [Running locally](#running-locally) / Maincloud section below). Use the same `<DATABASE_NAME>` as `SPACETIME_DATABASE` in the backend `.env`.
2. If **`spacetime publish`** to the existing name **fails** or the dashboard **still** shows `session_id` as PK, create a **new** database name, publish to that name, set `SPACETIME_DATABASE` to match in `.env`, and restart the backend. Old data stays in the old database; new writes go to the new append-only table. **Maincloud database names cannot contain underscores** — use e.g. `devopsaiv2` or `devopsai2`, not `devopsai_v2` (you will see `invalid characters in database name`).

---

## Architecture

| Piece | Location | Role |
|--------|-----------|------|
| SpacetimeDB server | Docker image `clockworklabs/spacetime:latest` | Runs `spacetime start`, listens on container port **3000**. |
| Module (Rust → WASM) | [`spacetimedb/devops-module/`](../spacetimedb/devops-module/) | Defines tables, reducers, and server-side log trim. |
| One-shot publish | Compose service **`st-init`** | `spacetime publish $SPACETIME_DATABASE` so the database exists before **`backend`** starts. |
| Backend client | [`backend/app/persistence.py`](../backend/app/persistence.py) | Calls reducers and SQL over HTTP. |

Startup order in [`infra/docker-compose.yml`](../infra/docker-compose.yml):

1. **`spacetime`** becomes healthy (`GET /v1/ping`).
2. **`st-init`** completes successfully (publish + WASM build on first run; can take several minutes).
3. **`backend`** starts with `depends_on` those two.

---

## Schema and reducers (module)

Source: [`spacetimedb/devops-module/src/lib.rs`](../spacetimedb/devops-module/src/lib.rs).

**Public tables**

- **`logs`** — `id` (auto-increment primary key), `time`, `service`, `level`, `message`, `extra_json` (JSON string, often `"{}"`).
- **`session_runbook_history`** — `id` (auto-increment primary key), `session_id`, `last_sanitized`, `last_sanitized_hash`.

**Reducers**

- **`ingest_log(time, service, level, message, extra_json)`** — inserts a row, then trims oldest rows so at most **2000** events remain (constant `LOG_BUFFER_MAX` in Rust; keep in sync with backend `LOG_BUFFER_MAX` env).
- **`append_session_runbook(session_id, last_sanitized, last_sanitized_hash)`** — inserts a new row (full history per session; PK is `id`, not `session_id`).

---

## Environment variables

| Variable | Purpose |
|----------|---------|
| `SPACETIME_DATABASE` | Logical database name; must match what you pass to `spacetime publish` (default `devopsai`). |
| `SPACETIME_HTTP_URL` | Base URL for the HTTP client **without** trailing path segments. **Inside Compose:** `http://spacetime:3000`. **On the host** talking to the published port: `http://localhost:3004` (host **3004** maps to container **3000**). |
| `SPACETIME_HTTP_TIMEOUT` | Optional; seconds for `httpx` client (default `60` in code). |
| `LOG_BUFFER_MAX` | Max lines the backend uses when tailing logs for API/UI; should match the module’s trim policy (default **2000**). |

See also [`.env.example`](../.env.example).

---

## How the backend uses SpacetimeDB

| Python function | SpacetimeDB |
|-----------------|-------------|
| `append_log_event` | `POST /v1/database/{db}/call/ingest_log` with JSON array body `[time, service, level, message, extra_json]`. |
| `fetch_log_tail` | `POST /v1/database/{db}/sql` with body `SELECT * FROM logs`; rows sorted **in Python** by `id`, then last N taken (SpacetimeDB SQL subset used here does not rely on `ORDER BY` in SQL). |
| `append_session_runbook` | `POST .../call/append_session_runbook` with JSON `[session_id, last_sanitized, last_sanitized_hash]`. |
| `get_session_runbook` | Tries `SELECT * ... WHERE session_id = '...' ORDER BY id DESC LIMIT 1` first, then falls back to unqualified `SELECT *` if the server returns 400. Maps columns by **session_id** + **64-char hash** when possible; otherwise infers **id** / **sanitized** / **hash**. Picks latest row by **max parsed id**, else **last row** in the result set. |

Lifespan in [`backend/app/main.py`](../backend/app/main.py) constructs an `httpx.AsyncClient` with `base_url=SPACETIME_HTTP_URL` so paths are relative to that base.

**Ingest path:** `POST /ingest` → `append_log_event` → SpacetimeDB → `broadcast_log` to WebSocket clients (live UI does not depend on polling SpacetimeDB for every viewer once connected, but reconnect uses `fetch_log_tail`).

---

## Running locally

### Full stack (recommended)

From repo root:

```bash
cp .env.example .env
# set GEMINI_API_KEY; SPACETIME_* defaults are fine for Compose
cd web && npm ci && npm run build
cd ..
docker compose -f infra/docker-compose.yml --env-file .env up --build
```

Check SpacetimeDB from the host:

```bash
curl -sf http://localhost:3004/v1/ping
```

### Backend on host + SpacetimeDB on host

1. Install the [SpacetimeDB CLI](https://spacetimedb.com/docs) and run `spacetime start` (default HTTP often on port **3000**).
2. Publish the module (database name must match `SPACETIME_DATABASE`):

   ```bash
   cd spacetimedb/devops-module
   spacetime publish devopsai -y
   ```

3. Export `SPACETIME_HTTP_URL=http://127.0.0.1:3000` and `SPACETIME_DATABASE=devopsai`.
4. Run Uvicorn as in the main [README](../README.md).

---

## Changing the module

After editing Rust under `spacetimedb/devops-module/`:

- Re-run **`st-init`** (e.g. `docker compose ... up --build` with `st-init` not skipped) or run `spacetime publish` manually against the same `SPACETIME_DATABASE`.

Clean WASM build artifacts are ignored via `.gitignore` (`spacetimedb/**/target/`).

---

## Troubleshooting

| Symptom | Things to check |
|---------|-------------------|
| Backend fails on startup with HTTP errors to Spacetime | Is `spacetime` healthy? `curl` `/v1/ping`. Did **`st-init`** finish? Wrong `SPACETIME_HTTP_URL` (Compose vs host port **3004**). |
| Empty logs in UI | Services must POST to `/ingest`; confirm `SPACETIME_DATABASE` matches published name. |
| Slow first `docker compose up` | First `spacetime publish` compiles Rust to WASM; wait for **`st-init`** to complete. |
| `LOG_BUFFER_MAX` mismatch | Align Rust `LOG_BUFFER_MAX` in `lib.rs` with backend env so behavior matches expectations. |

### Windows: `spacetime` is not recognized after install

**You usually do not need the Spacetime CLI on Windows for this repo.** The full stack uses Docker Compose: the **`spacetime`** and **`st-init`** services run the server and `spacetime publish` inside containers. Run `docker compose -f infra/docker-compose.yml --env-file .env up --build` from the repo root and use `http://localhost:3004/v1/ping` on the host to check the server.

If you still need the **local** CLI (e.g. manual `spacetime start` / publish on the host):

1. **Restart the terminal** (or sign out and back in). Installers often update PATH; the current PowerShell session may not see it yet.
2. **Reload PATH** in the current session, then try again:
   ```powershell
   $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
   spacetime --version
   ```
3. **Confirm the binary exists** — the installer may not use `%USERPROFILE%\.spacetime\bin`. Search:
   ```powershell
   where.exe spacetime 2>$null
   Get-ChildItem -Path $env:USERPROFILE -Filter spacetime.exe -Recurse -ErrorAction SilentlyContinue | Select-Object -First 3 FullName
   ```
4. If nothing is found, **re-run** the [official Windows install](https://spacetimedb.com/docs) or use **WSL/Linux** for CLI workflows.

---

## Maincloud (hosted SpacetimeDB)

1. **Log in** (once): `spacetime login` — links the CLI to your [spacetimedb.com](https://spacetimedb.com/) account.
2. **Publish** the module (database name must match `SPACETIME_DATABASE`, default `devopsai`):

   ```bash
   cd spacetimedb/devops-module
   spacetime publish devopsai --server maincloud -y
   ```

   On **Windows**, `spacetime publish` runs `cargo build` for `wasm32-unknown-unknown`. Build scripts run on the **host** and need the **MSVC linker** (`link.exe`). If you see **`link.exe` not found**, either install **Visual Studio Build Tools** with the **Desktop development with C++** workload, or **avoid local Rust entirely** and publish via Docker (Linux toolchain inside the image):

   ```powershell
   # Optional: publish to a new DB name (no underscores on Maincloud)
   $env:SPACETIME_DATABASE = "devopsaiv2"
   .\scripts\publish-maincloud-docker.ps1
   ```

   Config is read from `%LOCALAPPDATA%\SpacetimeDB\config` and mounted at `/home/spacetime/.config/spacetime` in the container.

3. **Point the backend** at Maincloud in `.env`:

   - `SPACETIME_HTTP_URL=https://maincloud.spacetimedb.com`
   - `SPACETIME_BEARER_TOKEN=<spacetimedb_token from cli.toml>` — same long JWT as in your local SpacetimeDB config after `spacetime login` (required for reducer/SQL calls on Maincloud in this setup).

4. **Run Compose without** the embedded `spacetime` / `st-init` services — they are behind profile `local-spacetime`. Use:

   ```bash
   docker compose -f infra/docker-compose.yml --env-file .env up -d --build
   ```

   Or `npm run stack:up:maincloud`. For the **embedded local** DB instead, use `npm run stack:up` / `stack:up:detached` (adds `--profile local-spacetime`).

Dashboard link after publish is printed by the CLI (e.g. `https://spacetimedb.com/devopsai`).

---

## Further reading

- Main project README: [README.md](../README.md)
- SpacetimeDB documentation: [https://spacetimedb.com/docs](https://spacetimedb.com/docs)
