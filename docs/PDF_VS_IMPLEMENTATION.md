# Executive Summary (PDF) vs this repository

This document maps claims in **Executive Summary (2)** to concrete implementation. It avoids regenerating the PDF; update the Word/PDF separately if you want diagrams to say “FastAPI” instead of “Node”.

| PDF topic | Implementation |
|-----------|----------------|
| Dockerized auth, payment, frontend | [`services/auth-service`](services/auth-service), [`services/payment-service`](services/payment-service), [`services/frontend-service`](services/frontend-service), [`infra/docker-compose.yml`](infra/docker-compose.yml) |
| Prometheus + Grafana | [`infra/prometheus.yml`](infra/prometheus.yml), [`infra/grafana/`](infra/grafana/) |
| Log ingestion to backend | Services POST to `POST /ingest`; persisted in **SpacetimeDB** (PDF described an in-memory buffer; we went further) — [`backend/app/persistence.py`](backend/app/persistence.py), [`spacetimedb/devops-module`](spacetimedb/devops-module) |
| Backend server | **FastAPI (Python)**, not Node — [`backend/app/main.py`](backend/app/main.py) |
| Gemini analysis + runbook | [`backend/app/gemini_client.py`](backend/app/gemini_client.py); model from `GEMINI_MODEL` (e.g. `gemini-2.0-flash`), not a fixed “Gemini 3” product name |
| Policy (VeriGuard-style) | [`backend/app/policy.py`](backend/app/policy.py) + JIT in [`backend/app/executor.py`](backend/app/executor.py) |
| Execution (Docker) | Docker socket in backend container; **not** Kubernetes — same control-plane *idea* as the PDF diagram |
| Live Prometheus context for analysis | `include_prometheus_snapshot` on `POST /analyze` + `PROMETHEUS_URL` — [`backend/app/prometheus_snapshot.py`](backend/app/prometheus_snapshot.py) |
| Web UI | React + Vite [`web/`](web/) |
| CI/CD | [`.github/workflows/ci.yml`](.github/workflows/ci.yml) |
| Optional cloud VM | Terraform sample [`infra/terraform/ec2-docker`](infra/terraform/ec2-docker/README.md), README “Cloud VM” section |
| Evaluation / MTTR proxy | [`scripts/eval-mttr.sh`](scripts/eval-mttr.sh) |
| Sample “realistic” log replay | [`samples/incident-sample.jsonl`](samples/incident-sample.jsonl), [`scripts/replay-sample-logs.sh`](scripts/replay-sample-logs.sh) |

**Intentionally not replicated as production infrastructure:** Fluentd/Filebeat pipelines (HTTP ingest is sufficient for the demo), importing Loghub datasets wholesale, full Kubernetes executor, formal verification of runbooks beyond rule-based checks.
