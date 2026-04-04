#!/usr/bin/env bash
# Proxy MTTR demo: time from start through successful /analyze (PDF evaluation metric).
# Optional: bash scripts/fault-inject.sh cpu (requires docker on host).
# Usage: BASE_URL=http://127.0.0.1:8000 bash scripts/eval-mttr.sh

set -euo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

PY=python3
command -v "$PY" >/dev/null 2>&1 || PY=python

start_ms=$("$PY" -c "import time; print(int(time.time()*1000))")

echo "==> T0=${start_ms}ms (eval-mttr start)"

if [[ "${INJECT_FAULT:-0}" == "1" ]] && command -v docker >/dev/null 2>&1; then
  echo "==> Optional fault: CPU stress on payment-service"
  bash "${REPO_ROOT}/scripts/fault-inject.sh" cpu 2>/dev/null || true
fi

echo "==> Wait for ${BASE_URL}/health"
start_ts=$(date +%s)
while true; do
  if curl -sf "${BASE_URL}/health" >/dev/null; then
    echo "backend ready"
    break
  fi
  if [[ $(($(date +%s) - start_ts)) -ge 120 ]]; then
    echo "ERROR: backend not ready within 120s"
    exit 1
  fi
  sleep 1
done

echo "==> POST /analyze (include_prometheus_snapshot=true)"
RESP=$(curl -sf -X POST "${BASE_URL}/analyze" -H "Content-Type: application/json" \
  -d '{"incident_description":"Payment 503 errors during demo eval","include_logs":true,"include_metrics_hint":"","include_prometheus_snapshot":true}')

end_ms=$("$PY" -c "import time; print(int(time.time()*1000))")
elapsed=$((end_ms - start_ms))

blocked_count=$(echo "$RESP" | "$PY" -c "import sys,json; d=json.load(sys.stdin); print(len(d.get('preview',{}).get('blocked',[])))")

echo "$RESP" | "$PY" -c "import sys,json; d=json.load(sys.stdin); assert d.get('approved_hash'), d" >/dev/null

echo "==> analyze_ok=true"
echo "==> mttr_proxy_ms=${elapsed}"
echo "==> blocked_lines_count=${blocked_count}"
echo "==> Tip: run with INJECT_FAULT=1 for optional CPU fault before analyze"
