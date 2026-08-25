<#
.SYNOPSIS
    Run the Sentinel edge worker on this machine.

.DESCRIPTION
    The worker lives in services/edge-worker and imports its own `app` package,
    so `python -m app.worker` only resolves from inside that directory - and
    only with the project venv, which is where torch, ultralytics and OpenCV
    are installed. Getting either wrong gives you "No module named 'app'" or a
    missing-torch error, so this script settles both and passes everything else
    straight through.

.EXAMPLE
    .\scripts\edge-worker.ps1 --all-cameras --forever

.EXAMPLE
    .\scripts\edge-worker.ps1 --camera SENTINEL-TRAFFIC-AHM-0001 --max-frames 40

.EXAMPLE
    # No model, no CV stack - just exercise the pipeline.
    .\scripts\edge-worker.ps1 --camera SENTINEL-TRAFFIC-AHM-0001 --synthetic
#>
[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $WorkerArgs
)

$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root '.venv\Scripts\python.exe'
$workerDir = Join-Path $root 'services\edge-worker'
$weightsDir = Join-Path $root 'weights'

if (-not (Test-Path $python)) {
    Write-Error @"
No project virtualenv at $python

Create one and install the worker's dependencies:
    python -m venv .venv
    .\.venv\Scripts\python.exe -m pip install -r services\edge-worker\requirements.txt

For real YOLO inference, also install the analytics extras (see docs\yolo-setup.md):
    .\.venv\Scripts\python.exe -m pip install torch --index-url https://download.pytorch.org/whl/cpu
    .\.venv\Scripts\python.exe -m pip install -r services\edge-worker\requirements-yolo.txt
"@
    exit 1
}

if (-not $WorkerArgs -or $WorkerArgs.Count -eq 0) {
    Write-Host 'Give the worker something to do, for example:' -ForegroundColor Yellow
    Write-Host '    .\scripts\edge-worker.ps1 --all-cameras --forever'
    Write-Host '    .\scripts\edge-worker.ps1 --camera SENTINEL-TRAFFIC-AHM-0001 --max-frames 40'
    exit 2
}

# The central API is reachable on localhost from the host; inside compose it is
# the service name, which is why the default in the code differs from this one.
if (-not $env:CENTRAL_API_URL) { $env:CENTRAL_API_URL = 'http://localhost:8000' }
$env:YOLO_WEIGHTS_DIR = $weightsDir

# Only claim YOLO when the weights are actually here. Defaulting it on and
# failing at load would be a worse first run than starting on the mock.
$weightsFile = Join-Path $weightsDir 'yolo11n.pt'
if (-not $env:YOLO_ENABLE) {
    if (Test-Path $weightsFile) {
        $env:YOLO_ENABLE = 'true'
    } else {
        $env:YOLO_ENABLE = 'false'
        Write-Host "No weights at $weightsFile - running the mock detector." -ForegroundColor Yellow
        Write-Host 'See docs\yolo-setup.md to install the real model.' -ForegroundColor Yellow
    }
}

# Resolve any path argument BEFORE changing directory. The worker runs from
# services/edge-worker, so a relative --clip given at the repo root would
# otherwise resolve against the wrong place and fail to open.
for ($i = 0; $i -lt $WorkerArgs.Count; $i++) {
    if ($WorkerArgs[$i] -in @('--clip') -and ($i + 1) -lt $WorkerArgs.Count) {
        $candidate = $WorkerArgs[$i + 1]
        if (-not [System.IO.Path]::IsPathRooted($candidate)) {
            $resolved = Join-Path (Get-Location).Path $candidate
            if (Test-Path $resolved) { $WorkerArgs[$i + 1] = (Resolve-Path $resolved).Path }
        }
    }
}

Write-Host "edge-worker -> $($env:CENTRAL_API_URL)  (YOLO_ENABLE=$($env:YOLO_ENABLE))" -ForegroundColor Cyan

Push-Location $workerDir
try {
    & $python -m app.worker @WorkerArgs
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
