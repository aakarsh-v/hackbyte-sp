#!/usr/bin/env bash
# Replay synthetic incident lines from samples/incident-sample.jsonl into POST /ingest.
# Usage: from repo root, BASE_URL=http://127.0.0.1:8000 bash scripts/replay-sample-logs.sh

set -euo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SAMPLE="${REPO_ROOT}/samples/incident-sample.jsonl"

if [[ ! -f "$SAMPLE" ]]; then
  echo "ERROR: missing $SAMPLE"
  exit 1
fi

echo "==> Replaying $(wc -l < "$SAMPLE") lines to ${BASE_URL}/ingest"
while IFS= read -r line || [[ -n "$line" ]]; do
  [[ -z "$line" ]] && continue
  curl -sf -X POST "${BASE_URL}/ingest" -H "Content-Type: application/json" -d "$line" >/dev/null
  echo "  ingested OK"
done < "$SAMPLE"
echo "==> Done"
