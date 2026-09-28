#!/usr/bin/env bash
# Phase 0 smoke test: verifies that the running stack answers on both ports.
set -euo pipefail

API_URL="${API_URL:-http://localhost:8000}"
WEB_URL="${WEB_URL:-http://localhost:3000}"

echo "==> API ${API_URL}/api/v1/health"
health=$(curl -fsS "${API_URL}/api/v1/health")
echo "${health}"

echo "${health}" | grep -q '"redis":{"status":"ok"' \
  || { echo "FAIL: redis is not ok"; exit 1; }
echo "${health}" | grep -q '"workers":{"status":"ok"' \
  || { echo "FAIL: no RQ worker registered"; exit 1; }

echo "==> Frontend ${WEB_URL}"
curl -fsS -o /dev/null "${WEB_URL}"

echo "OK: api, redis, worker and frontend are up"
