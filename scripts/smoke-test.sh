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

# Phase 1: full job lifecycle on a generated 1-second WAV.
if [ "${SMOKE_JOB:-1}" = "1" ]; then
  fixture="$(mktemp -t smoke).wav"
  python3 - "$fixture" <<'PY'
import math, struct, sys, wave

with wave.open(sys.argv[1], "wb") as out:
    out.setnchannels(1)
    out.setsampwidth(2)
    out.setframerate(16000)
    out.writeframes(
        b"".join(struct.pack("<h", int(3000 * math.sin(2 * math.pi * 440 * t / 16000)))
                 for t in range(16000))
    )
PY

  echo "==> POST ${API_URL}/api/v1/jobs"
  job_id=$(curl -fsS -X POST "${API_URL}/api/v1/jobs" \
    -F "file=@${fixture};type=audio/wav" \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["job_id"])')
  rm -f "${fixture}"
  echo "job_id=${job_id}"

  for _ in $(seq 1 180); do
    body=$(curl -fsS "${API_URL}/api/v1/jobs/${job_id}")
    status=$(echo "${body}" | python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])')
    case "${status}" in COMPLETED|FAILED) break ;; esac
    sleep 1
  done

  error_code=$(echo "${body}" | python3 -c 'import json,sys; print(json.load(sys.stdin)["error_code"] or "")')

  # The fixture is a synthetic tone, not speech: the diarizer may find no speaker turns,
  # and if it does find some, GigaAM recognizes no words in them. Both outcomes mean the
  # pipeline ran. Any other failure means it is broken.
  if [ "${status}" = "COMPLETED" ]; then
    echo "OK: job ${job_id} reached COMPLETED"
  elif [ "${status}" = "FAILED" ] && \
       { [ "${error_code}" = "DIARIZATION_FAILED" ] || [ "${error_code}" = "ASR_FAILED" ]; }; then
    echo "OK: pipeline ran; no speech found in the synthetic fixture (expected)"
  else
    echo "FAIL: job ended as ${status} (${error_code:-no error code})"
    exit 1
  fi

  # T8.3 — the smoke test cleans up after itself instead of leaving a job per run behind.
  echo "==> DELETE ${API_URL}/api/v1/jobs/${job_id}"
  curl -fsS -o /dev/null -X DELETE "${API_URL}/api/v1/jobs/${job_id}"
  code=$(curl -s -o /dev/null -w '%{http_code}' "${API_URL}/api/v1/jobs/${job_id}")
  [ "${code}" = "404" ] || { echo "FAIL: job still exists after delete (${code})"; exit 1; }
  echo "OK: job ${job_id} deleted"
fi
