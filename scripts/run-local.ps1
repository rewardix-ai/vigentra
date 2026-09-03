<#
.SYNOPSIS
    Run the whole Vigentra demo stack on one machine, without Docker.

.DESCRIPTION
    `docker compose up --build` is still the supported way to run this and is
    what the README documents. This script exists for the case Docker Desktop
    is not running: it starts the same four services directly, on the same
    ports, reading the same `.env`.

    Two differences from the Compose stack, both addressing rather than
    behaviour:

      * SQLite instead of Postgres. The schema is identical - the models
        declare JSONB with a JSON variant precisely so the suite and this
        script can run without a database container.
      * `127.0.0.1` instead of Compose service names, which only resolve
        inside the Compose network.

    Ports: 8000 central-api, 8001 traffic-vms, 8002 municipal-vms,
    3000 dashboard.

.PARAMETER Fresh
    Delete the local SQLite file first, so the registry is rebuilt from the
    department systems on startup.

.PARAMETER SkipDashboard
    Start only the APIs. Useful when driving the API directly.

.PARAMETER SkipAnpr
    Do not start the edge worker. Counting stops when it is not running, so
    the default is to run it - a count is only meaningful if something has
    been counting continuously, and nobody remembers to start it by hand.

.EXAMPLE
    .\scripts\run-local.ps1 -Fresh
#>
[CmdletBinding()]
param(
    [switch]$Fresh,
    [switch]$SkipDashboard,
    [switch]$SkipAnpr
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Test-Path (Join-Path $root '.env'))) {
    Write-Error "No .env at the repo root. Copy .env.example and set the local addresses, or see the header of this script."
}

if ($Fresh) {
    $db = Join-Path $root 'vigentra-local.sqlite'
    if (Test-Path $db) {
        Remove-Item $db -Force
        Write-Host "removed $db" -ForegroundColor DarkGray
    }
}

# The grid adapter and the vehicle registry read these from the process
# environment, not through pydantic settings, so a .env entry is not enough -
# and a relative path would resolve against each service's own working
# directory rather than the repo root.
$env:SENTINEL_GRID_REFERENCE = Join-Path $root 'data\reference\grid_cameras.json'
$env:VEHICLE_REGISTRY_PATH   = Join-Path $root 'data\reference\vehicle_registry.json'

# Each mock hands Vigentra an absolute URL to its own media, and central-api
# fetches that URL server-side. The default is the Compose service name, which
# resolves only inside the Compose network - outside it every department-VMS
# feed dies as VIDEO_SOURCE_UNAVAILABLE with nothing on screen but "the stream
# stopped". These are read with os.getenv, so a .env entry is not enough.
$env:TRAFFIC_VMS_PUBLIC_BASE_URL   = 'http://127.0.0.1:8001'
$env:MUNICIPAL_VMS_PUBLIC_BASE_URL = 'http://127.0.0.1:8002'
# Same reason as the two above: the dashboard's proxy routes default to
# http://central-api:8000, which only resolves inside the Compose network.
# Without this every /api call from the browser comes back 502.
$env:CENTRAL_API_URL               = 'http://127.0.0.1:8000'

$failed = @()

$services = @(
    @{ Name = 'traffic-vms';   Dir = 'services\traffic-vms';   Port = 8001 },
    @{ Name = 'municipal-vms'; Dir = 'services\municipal-vms'; Port = 8002 },
    @{ Name = 'central-api';   Dir = 'services\central-api';   Port = 8000 }
)

foreach ($service in $services) {
    Write-Host "starting $($service.Name) on :$($service.Port)" -ForegroundColor Cyan
    # -ArgumentList joins an array with spaces and quotes NOTHING, so a repo
    # path containing a space (D:\CCTV Management\...) reaches uvicorn as two
    # arguments and it exits before binding. Quote it here.
    $arguments = @(
        '-m', 'uvicorn', 'app.main:app',
        '--host', '127.0.0.1',
        '--port', $service.Port,
        '--app-dir', ('"{0}"' -f (Join-Path $root $service.Dir))
    )
    # Working directory stays the repo root so `.env` and the relative
    # reference paths resolve the same way for every service.
    Start-Process -FilePath 'python' -ArgumentList $arguments -WorkingDirectory $root -WindowStyle Hidden
}

Write-Host 'waiting for the APIs to answer...' -ForegroundColor DarkGray
foreach ($service in $services) {
    $url = "http://127.0.0.1:$($service.Port)/docs"
    $ready = $false
    foreach ($attempt in 1..30) {
        try {
            $null = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 2
            $ready = $true
            break
        } catch {
            Start-Sleep -Milliseconds 700
        }
    }
    if ($ready) {
        Write-Host "  ok   $($service.Name)  $url" -ForegroundColor Green
    } else {
        $failed += $service.Name
        Write-Warning "  $($service.Name) did not answer on :$($service.Port)"
    }
}

if (-not $SkipDashboard) {
    $dashboard = Join-Path $root 'services\dashboard'
    if (-not (Test-Path (Join-Path $dashboard 'node_modules'))) {
        Write-Host 'installing dashboard dependencies (first run only)' -ForegroundColor Cyan
        Start-Process -FilePath 'npm.cmd' -ArgumentList @('install', '--no-audit', '--no-fund') `
            -WorkingDirectory $dashboard -Wait -NoNewWindow
    }
    Write-Host 'starting dashboard on :3000' -ForegroundColor Cyan
    # 'npm' resolves to npm.ps1 on Windows, which Start-Process cannot launch
    # directly - it needs the .cmd shim.
    Start-Process -FilePath 'npm.cmd' -ArgumentList @('run', 'dev') `
        -WorkingDirectory $dashboard -WindowStyle Hidden
    foreach ($attempt in 1..40) {
        try {
            $null = Invoke-WebRequest -Uri 'http://127.0.0.1:3000/login' -UseBasicParsing -TimeoutSec 2
            break
        } catch { Start-Sleep -Milliseconds 750 }
    }
}

Write-Host ''
if ($failed.Count -gt 0) {
    # Reporting success here regardless of the warnings above is how a broken
    # stack reaches the browser looking like a dashboard bug.
    Write-Warning ("did not start: {0}" -f ($failed -join ', '))
    Write-Warning 'The stack is NOT fully up. Check the ports below before signing in.'
} else {
    Write-Host 'Vigentra is up:' -ForegroundColor Green
}
Write-Host '  Dashboard      http://localhost:3000'
Write-Host '  Central API    http://localhost:8000/docs'
Write-Host '  Traffic VMS    http://localhost:8001/docs'
Write-Host '  Municipal VMS  http://localhost:8002/docs'
Write-Host ''
Write-Host 'Sign in with joint.control / Joint@2026 to watch every camera -'
Write-Host 'see the sign-in page for the rest.'
# ---------------------------------------------------------------------------
# Edge analytics, always on.
#
# The worker is a separate process that writes detections straight to the
# database. Nothing about it depends on the dashboard being open or even
# running: close the browser, restart Next.js, and the counting carries on -
# which is the whole point of counting centrally rather than in a page.
#
# It runs whenever the models are present. Without them it would start,
# discover it cannot load a detector and spin, so it is skipped with a line
# saying why rather than left to fail quietly.
# ---------------------------------------------------------------------------
if (-not $SkipAnpr) {
    # Same resolution edge-worker.ps1 uses: the plate models ship beside the
    # ANPR package, and fall back to the vendored D:\ANPR checkout.
    $anprModels = Join-Path $root 'services\edge-worker\models'
    if (-not (Test-Path (Join-Path $anprModels 'plate_detector.pt'))) {
        if (Test-Path 'D:\ANPR\models\plate_detector.pt') { $anprModels = 'D:\ANPR\models' }
    }
    $env:ANPR_MODELS_DIR = $anprModels

    # The grid is behind an access password now, and the worker reads it from
    # the environment rather than through pydantic settings, so a .env entry
    # alone does not reach it.
    $envFile = Join-Path $root '.env'
    if (Test-Path $envFile) {
        foreach ($line in Get-Content $envFile) {
            if ($line -match '^\s*SENTINEL_GRID_(PASSWORD|BASE_URL|RTSP_HOST)\s*=\s*(.*)$') {
                Set-Item -Path ("env:SENTINEL_GRID_" + $Matches[1]) -Value $Matches[2].Trim()
            }
        }
    }

    $plateModel = Join-Path $anprModels 'plate_detector.pt'
    if (Test-Path $plateModel) {
        Write-Host 'starting edge worker (all cameras, continuous)' -ForegroundColor Cyan
        $workerArgs = @(
            '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
            (Join-Path $PSScriptRoot 'edge-worker.ps1'),
            '--all-cameras', '--forever',
            '--max-frames', '20', '--sample-interval', '20', '--cycle-seconds', '120'
        )
        # Logged to a file, not swallowed. A hidden window with no log is how
        # a worker that dies on startup looks exactly like a worker that is
        # running and finding nothing - which cost an afternoon.
        $logDir = Join-Path $root 'logs'
        if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }
        $workerLog = Join-Path $logDir 'edge-worker.log'
        Start-Process -FilePath 'powershell.exe' -ArgumentList $workerArgs `
            -WorkingDirectory $root -WindowStyle Hidden `
            -RedirectStandardOutput $workerLog `
            -RedirectStandardError (Join-Path $logDir 'edge-worker.err.log')
        Write-Host "  log: $workerLog" -ForegroundColor DarkGray
    } else {
        Write-Host "No plate detector at $plateModel - ANPR not started." -ForegroundColor Yellow
        Write-Host 'Vehicles and plates will stay at zero until it is there.' -ForegroundColor Yellow
    }
}

Write-Host ''
Write-Host 'Stop everything with:  Get-Process python,node | Stop-Process -Force' -ForegroundColor DarkGray
