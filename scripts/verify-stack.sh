#!/usr/bin/env bash
# Full-stack verification: logs, metrics, policy preview, analyze, execute.
# Run from repo root after: docker compose -f infra/docker-compose.yml --env-file .env up (-d)
# Requires: bash, curl, Python 3 (or python). Linux/macOS/Git Bash on Windows.
# GEMINI_API_KEY optional — backend uses fallback templates when Gemini is unavailable.

set -euo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
PROM_URL="${PROM_URL:-http://127.0.0.1:9090}"
ST_URL="${ST_URL:-http://127.0.0.1:3004}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

PY=python3
command -v "$PY" >/dev/null 2>&1 || PY=python
command -v "$PY" >/dev/null 2>&1 || {
  echo "ERROR: python3 or python not found"
  exit 1
}

# First Spacetime publish can take many minutes (Rust/WASM). Override for CI: WAIT_SECS=1200
WAIT_SECS="${WAIT_SECS:-600}"
echo "==> Wait for backend health (up to ${WAIT_SECS}s)"
start_ts=$(date +%s)
while true; do
  if curl -sf "${BASE_URL}/health" >/dev/null; then
    echo "backend ready"
    break
  fi
  now_ts=$(date +%s)
  if [ $((now_ts - start_ts)) -ge "$WAIT_SECS" ]; then
    echo "ERROR: backend not reachable at ${BASE_URL} within ${WAIT_SECS}s"
    exit 1
  fi
  sleep 3
done

echo "==> SpacetimeDB ping"
curl -sf "${ST_URL}/v1/ping" >/dev/null || {
  echo "ERROR: SpacetimeDB not reachable at ${ST_URL}"
  exit 1
}

echo "==> Prometheus query (metrics visible)"
curl -sf "${PROM_URL}/api/v1/query?query=up" | "$PY" -c "import sys,json; d=json.load(sys.stdin); assert d.get('status')=='success', d"

echo "==> Ingest log line"
curl -sf -X POST "${BASE_URL}/ingest" -H "Content-Type: application/json" \
  -d '{"service":"payment-service","level":"ERROR","message":"HTTP 503 on /pay"}' >/dev/null

echo "==> Optional fault injection (ignore if docker unavailable)"
if command -v docker >/dev/null 2>&1; then
  bash "${REPO_ROOT}/scripts/fault-inject.sh" cpu 2>/dev/null || true
fi

echo "==> Policy preview (deterministic unsafe line)"
PP=$(curl -sf -X POST "${BASE_URL}/policy/preview" -H "Content-Type: application/json" \
  -d '{"script":"rm -rf /\necho safe"}')
echo "$PP" | "$PY" -c "import sys,json; d=json.load(sys.stdin); assert len(d.get('blocked',[]))>=1, d"

echo "==> Analyze + runbook"
RESP=$(curl -sf -X POST "${BASE_URL}/analyze" -H "Content-Type: application/json" \
  -d '{"incident_description":"Payment service returning 503 errors","include_logs":true,"include_metrics_hint":"Prometheus: query up shows scrape targets"}')

echo "$RESP" | "$PY" -c "import sys,json; d=json.load(sys.stdin); assert d.get('approved_hash'), d"

echo "==> Execute approved runbook"
EX=$(echo "$RESP" | "$PY" -c 'import json,sys; d=json.load(sys.stdin); json.dump({"content": "\n".join(d["preview"]["sanitized_lines"]), "content_hash": d["approved_hash"]}, sys.stdout)' | curl -sf -X POST "${BASE_URL}/execute" -H "Content-Type: application/json" --data-binary @-)
echo "$EX" | "$PY" -c "import sys,json; d=json.load(sys.stdin); assert 'steps_run' in d and 'output' in d, d"

echo "==> Stack verification completed OK"
exit 0
