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

# Phase 1: full job lifecycle on a generated 1-second WAV.
if ($env:SMOKE_JOB -ne "0") {
    $fixture = Join-Path ([System.IO.Path]::GetTempPath()) "smoke-$([guid]::NewGuid()).wav"
    python3 -c @"
import math, struct, sys, wave
with wave.open(sys.argv[1], 'wb') as out:
    out.setnchannels(1); out.setsampwidth(2); out.setframerate(16000)
    out.writeframes(b''.join(struct.pack('<h', int(3000 * math.sin(2 * math.pi * 440 * t / 16000))) for t in range(16000)))
"@ $fixture

    Write-Host "==> POST $ApiUrl/api/v1/jobs"
    $form = @{ file = Get-Item $fixture }
    $created = Invoke-RestMethod -Uri "$ApiUrl/api/v1/jobs" -Method Post -Form $form -TimeoutSec 120
    Remove-Item $fixture -Force
    Write-Host "job_id=$($created.job_id)"

    $status = ""
    for ($i = 0; $i -lt 60; $i++) {
        $job = Invoke-RestMethod -Uri "$ApiUrl/api/v1/jobs/$($created.job_id)" -TimeoutSec 10
        $status = $job.status
        if ($status -eq "COMPLETED") { break }
        if ($status -eq "FAILED") { throw "FAIL: job failed" }
        Start-Sleep -Seconds 1
    }
    if ($status -ne "COMPLETED") { throw "FAIL: job did not complete (last: $status)" }
    Write-Host "OK: job $($created.job_id) reached COMPLETED"
}
