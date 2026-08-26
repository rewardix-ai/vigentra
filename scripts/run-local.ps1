<#
.SYNOPSIS
    Run the whole Sentinel demo stack on one machine, without Docker.

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

.EXAMPLE
    .\scripts\run-local.ps1 -Fresh
#>
[CmdletBinding()]
param(
    [switch]$Fresh,
    [switch]$SkipDashboard
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Test-Path (Join-Path $root '.env'))) {
    Write-Error "No .env at the repo root. Copy .env.example and set the local addresses, or see the header of this script."
}

if ($Fresh) {
    $db = Join-Path $root 'sentinel-local.sqlite'
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

# Each mock hands Sentinel an absolute URL to its own media, and central-api
# fetches that URL server-side. The default is the Compose service name, which
# resolves only inside the Compose network - outside it every department-VMS
# feed dies as VIDEO_SOURCE_UNAVAILABLE with nothing on screen but "the stream
# stopped". These are read with os.getenv, so a .env entry is not enough.
$env:TRAFFIC_VMS_PUBLIC_BASE_URL   = 'http://127.0.0.1:8001'
$env:MUNICIPAL_VMS_PUBLIC_BASE_URL = 'http://127.0.0.1:8002'

$services = @(
    @{ Name = 'traffic-vms';   Dir = 'services\traffic-vms';   Port = 8001 },
    @{ Name = 'municipal-vms'; Dir = 'services\municipal-vms'; Port = 8002 },
    @{ Name = 'central-api';   Dir = 'services\central-api';   Port = 8000 }
)

foreach ($service in $services) {
    Write-Host "starting $($service.Name) on :$($service.Port)" -ForegroundColor Cyan
    $arguments = @(
        '-m', 'uvicorn', 'app.main:app',
        '--host', '127.0.0.1',
        '--port', $service.Port,
        '--app-dir', (Join-Path $root $service.Dir)
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
        Write-Warning "  $($service.Name) did not answer on :$($service.Port)"
    }
}

if (-not $SkipDashboard) {
    $dashboard = Join-Path $root 'services\dashboard'
    if (-not (Test-Path (Join-Path $dashboard 'node_modules'))) {
        Write-Host 'installing dashboard dependencies (first run only)' -ForegroundColor Cyan
        Start-Process -FilePath 'npm' -ArgumentList @('install', '--no-audit', '--no-fund') `
            -WorkingDirectory $dashboard -Wait -NoNewWindow
    }
    Write-Host 'starting dashboard on :3000' -ForegroundColor Cyan
    Start-Process -FilePath 'npm' -ArgumentList @('run', 'dev') `
        -WorkingDirectory $dashboard -WindowStyle Hidden
}

Write-Host ''
Write-Host 'Sentinel is up:' -ForegroundColor Green
Write-Host '  Dashboard      http://localhost:3000'
Write-Host '  Central API    http://localhost:8000/docs'
Write-Host '  Traffic VMS    http://localhost:8001/docs'
Write-Host '  Municipal VMS  http://localhost:8002/docs'
Write-Host ''
Write-Host 'Sign in with traffic.state / Traffic@2026 - see the sign-in page for the rest.'
Write-Host 'Stop everything with:  Get-Process python,node | Stop-Process -Force' -ForegroundColor DarkGray
