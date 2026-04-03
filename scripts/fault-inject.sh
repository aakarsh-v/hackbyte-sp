#!/usr/bin/env bash
# Fault injection for demo (run on the Docker host / VM, not inside containers).
# Usage: ./scripts/fault-inject.sh [cpu|kill-payment|fail-mode-on|fail-mode-off]

set -euo pipefail

case "${1:-help}" in
  cpu)
    echo "Stressing CPU inside payment-service (requires container name payment-service)..."
    docker exec payment-service sh -c "yes >/dev/null &" || true
    echo "Started background load. Stop with: docker exec payment-service pkill yes"
    ;;
  kill-payment)
    echo "Stopping payment-service container..."
    docker stop payment-service || true
    echo "Restart with: docker start payment-service"
    ;;
  fail-mode-on)
    echo "Enable FAIL_MODE on payment-service (503 on /pay) — recreate with compose after editing env."
    echo "Quick test: curl -s -o /dev/null -w '%{http_code}' -X POST http://localhost:8082/pay -H 'Content-Type: application/json' -d '{\"amount\":1}'"
    ;;
  fail-mode-off)
    echo "Set FAIL_MODE=0 in infra/docker-compose.yml for payment-service, then: docker compose up -d payment-service"
    ;;
  *)
    echo "Usage: $0 cpu | kill-payment | fail-mode-on | fail-mode-off"
    exit 1
    ;;
esac
