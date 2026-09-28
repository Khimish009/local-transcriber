# Phase 0 smoke test (Windows): verifies that the running stack answers on both ports.
$ErrorActionPreference = "Stop"

$ApiUrl = if ($env:API_URL) { $env:API_URL } else { "http://localhost:8000" }
$WebUrl = if ($env:WEB_URL) { $env:WEB_URL } else { "http://localhost:3000" }

Write-Host "==> API $ApiUrl/api/v1/health"
$health = Invoke-RestMethod -Uri "$ApiUrl/api/v1/health" -TimeoutSec 10
$health | ConvertTo-Json -Depth 5

if ($health.redis.status -ne "ok") { throw "FAIL: redis is not ok" }
if ($health.workers.status -ne "ok") { throw "FAIL: no RQ worker registered" }

Write-Host "==> Frontend $WebUrl"
Invoke-WebRequest -Uri $WebUrl -TimeoutSec 10 -UseBasicParsing | Out-Null

Write-Host "OK: api, redis, worker and frontend are up"
