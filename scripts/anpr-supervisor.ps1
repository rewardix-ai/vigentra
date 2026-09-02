<#
.SYNOPSIS
    Keep the edge worker running, restarting it if it dies.

.DESCRIPTION
    `--forever` makes the worker cycle its cameras indefinitely, and it now
    survives the ordinary interruptions on its own: a camera that fails, a
    registry that restarts under it, a feed that drops. What it cannot survive
    is being killed - and on a machine under memory pressure that is what
    eventually happens, silently and with no traceback, usually while something
    else is building.

    Analytics that stop when nobody is watching are not continuous analytics,
    and a count with an unexplained gap in it is worse than no count. This
    restarts the worker when it exits for any reason other than being asked to
    stop, with a short backoff so a worker that cannot start does not spin.

    One process per department, because an analytics account may only submit
    detections for cameras its own department owns. Run this twice, once per
    department, or use -Both to start the pair.

.PARAMETER Department
    traffic | municipal. Ignored when -Both is given.

.PARAMETER Both
    Start one supervisor per department, each in its own window.

.EXAMPLE
    .\scripts\anpr-supervisor.ps1 -Both

.EXAMPLE
    .\scripts\anpr-supervisor.ps1 -Department traffic -MaxFrames 40
#>
[CmdletBinding()]
param(
    [ValidateSet('traffic', 'municipal')]
    [string]$Department = 'traffic',
    [switch]$Both,
    [int]$MaxFrames = 25,
    [int]$SampleInterval = 20,
    [int]$CycleSeconds = 120
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot

$accounts = @{
    traffic   = @{ User = 'traffic.ai';   Pass = 'AiOps@2026' }
    municipal = @{ User = 'municipal.ai'; Pass = 'MuniOps@2026' }
}

if ($Both) {
    foreach ($dept in $accounts.Keys) {
        Start-Process -FilePath 'powershell.exe' -ArgumentList @(
            '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $PSCommandPath,
            '-Department', $dept,
            '-MaxFrames', $MaxFrames,
            '-SampleInterval', $SampleInterval,
            '-CycleSeconds', $CycleSeconds
        ) -WorkingDirectory $root
        Write-Host "supervising $dept in its own window" -ForegroundColor Cyan
    }
    Write-Host ''
    Write-Host 'Both departments supervised. Counts appear at:' -ForegroundColor Green
    Write-Host '  http://localhost:3000/reports/anpr'
    exit 0
}

$account = $accounts[$Department]
$python = Join-Path $root '.venv\Scripts\python.exe'
$workerDir = Join-Path $root 'services\edge-worker'
$logDir = Join-Path $root 'logs'

if (-not (Test-Path $python)) {
    Write-Error "No project virtualenv at $python - see scripts\edge-worker.ps1 for setup."
    exit 1
}
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

# The worker reads these from its environment. Everything not set here keeps
# the worker's own defaults, so this script does not become a second place
# where configuration lives.
if (-not $env:CENTRAL_API_URL) { $env:CENTRAL_API_URL = 'http://127.0.0.1:8000' }
$env:EDGE_USERNAME = $account.User
$env:EDGE_PASSWORD = $account.Pass
$env:KMP_DUPLICATE_LIB_OK = 'TRUE'

if (-not $env:ANPR_MODELS_DIR) {
    $bundled = Join-Path $workerDir 'models'
    if (Test-Path (Join-Path $bundled 'plate_detector.pt')) { $env:ANPR_MODELS_DIR = $bundled }
    elseif (Test-Path 'D:\ANPR\models\plate_detector.pt')   { $env:ANPR_MODELS_DIR = 'D:\ANPR\models' }
}
if (-not $env:ANPR_ENABLE) { $env:ANPR_ENABLE = 'true' }
if (-not $env:YOLO_ENABLE) { $env:YOLO_ENABLE = 'true' }

# Grid access comes from .env so the password is not duplicated here.
$envFile = Join-Path $root '.env'
if (Test-Path $envFile) {
    foreach ($key in 'SENTINEL_GRID_PASSWORD', 'SENTINEL_GRID_BASE_URL', 'SENTINEL_GRID_RTSP_HOST') {
        if (Get-Item "env:$key" -ErrorAction SilentlyContinue) { continue }
        $match = Get-Content $envFile | Select-String "^$key="
        if ($match) { Set-Item -Path "env:$key" -Value ($match[0].ToString() -split '=', 2)[1].Trim() }
    }
}

$workerArgs = @(
    '-m', 'app.worker', '--all-cameras', '--forever',
    '--max-frames', $MaxFrames,
    '--sample-interval', $SampleInterval,
    '--cycle-seconds', $CycleSeconds
)

$log = Join-Path $logDir "edge-$Department.log"
$errLog = Join-Path $logDir "edge-$Department.err.log"
$restarts = 0

Write-Host "supervising $Department ($($account.User)) - Ctrl+C to stop" -ForegroundColor Cyan
Write-Host "  log: $log" -ForegroundColor DarkGray

while ($true) {
    $started = Get-Date
    $process = Start-Process -FilePath $python -ArgumentList $workerArgs `
        -WorkingDirectory $workerDir `
        -RedirectStandardOutput $log -RedirectStandardError $errLog `
        -NoNewWindow -PassThru
    $process.WaitForExit()

    $ranFor = (Get-Date) - $started
    $restarts++

    # A worker that dies within seconds is misconfigured, not unlucky; backing
    # off further there avoids a restart loop that fills the disk with logs.
    $delay = if ($ranFor.TotalSeconds -lt 30) { [Math]::Min(300, 15 * $restarts) } else { 10 }

    Write-Host ("[{0}] {1} worker exited ({2}) after {3:N0}s - restarting in {4}s (restart #{5})" -f `
        (Get-Date -Format 'HH:mm:ss'), $Department, $process.ExitCode, $ranFor.TotalSeconds, $delay, $restarts) `
        -ForegroundColor Yellow

    Start-Sleep -Seconds $delay
}
