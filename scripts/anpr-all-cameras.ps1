<#
.SYNOPSIS
    Run ANPR across every camera in the federation.

.DESCRIPTION
    Two workers, one per department, because that is the boundary the platform
    enforces: an analytics account may only submit detections for cameras its
    own department owns. `traffic.ai` covers the Traffic Police cameras and
    `municipal.ai` the Municipal Corporation ones - together, the whole estate, with
    no account that can ingest across the boundary.

    Runs the two sequentially by default. On a machine with cores and RAM to
    spare, -Parallel starts both at once; on 8 GB it will thrash, because each
    worker holds torch, a vehicle model, a plate model and PaddleOCR.

.PARAMETER MaxFrames
    Frames sampled per camera per pass. ANPR runs about 0.7 fps on CPU, so 40
    frames is roughly a minute per camera - about 30 minutes for a full sweep.

.PARAMETER Forever
    Keep cycling instead of one pass.

.PARAMETER Parallel
    Start both departments at once rather than one after the other.

.EXAMPLE
    .\scripts\anpr-all-cameras.ps1
    .\scripts\anpr-all-cameras.ps1 -MaxFrames 20 -Forever
#>
[CmdletBinding()]
param(
    [int]$MaxFrames = 40,
    [switch]$Forever,
    [switch]$Parallel
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$worker = Join-Path $PSScriptRoot 'edge-worker.ps1'

$departments = @(
    @{ Name = 'Traffic Police';        User = 'traffic.ai';   Pass = 'AiOps@2026';   Cameras = 23 },
    @{ Name = 'Municipal Corporation'; User = 'municipal.ai'; Pass = 'MuniOps@2026'; Cameras = 7 }
)

$common = @('--all-cameras', '--max-frames', $MaxFrames)
if ($Forever) { $common += '--forever' }

Write-Host "ANPR sweep: $($departments.Count) departments, 30 cameras, $MaxFrames frames each" -ForegroundColor Cyan
if (-not $Parallel) {
    Write-Host 'Sequential - roughly a minute per camera on CPU. -Parallel to overlap.' -ForegroundColor DarkGray
}

foreach ($dept in $departments) {
    Write-Host ""
    Write-Host "$($dept.Name) - $($dept.User), $($dept.Cameras) cameras" -ForegroundColor Cyan

    # Each worker signs in as its own department's analytics account, so the
    # detections it submits carry that department's custody.
    $environment = @{
        EDGE_USERNAME = $dept.User
        EDGE_PASSWORD = $dept.Pass
    }

    if ($Parallel) {
        Start-Process -FilePath 'powershell.exe' -ArgumentList @(
            '-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command',
            "`$env:EDGE_USERNAME='$($dept.User)'; `$env:EDGE_PASSWORD='$($dept.Pass)'; & '$worker' $($common -join ' ')"
        ) -WorkingDirectory $root
        Write-Host "  started in its own window" -ForegroundColor DarkGray
    } else {
        foreach ($key in $environment.Keys) { Set-Item -Path "env:$key" -Value $environment[$key] }
        & $worker @common
    }
}

Write-Host ""
Write-Host 'Detections land in the registry as they are ingested:' -ForegroundColor Green
Write-Host '  http://localhost:3000/reports/anpr   plates and timestamps'
Write-Host '  http://localhost:3000/alerts         watchlist hits'
