param(
    [string]$Repo = "C:\CSP\Youtube",
    [string]$Python = "C:\CSP\venv\Scripts\python.exe",
    [string]$Output = "C:\CSP\output",
    [string]$Db = "C:\CSP\output\csp-studio.db",
    [int]$Port = 8765,
    [switch]$NoBrowser,
    [switch]$SkipWorker
)

$ErrorActionPreference = "Stop"

$Repo = [System.IO.Path]::GetFullPath($Repo)
$Python = [System.IO.Path]::GetFullPath($Python)
$Output = [System.IO.Path]::GetFullPath($Output)
$Db = [System.IO.Path]::GetFullPath($Db)
$StudioUrl = "http://127.0.0.1:$Port"
$ServiceDir = Join-Path $Output ".studio-service"

if (-not (Test-Path -LiteralPath $Repo -PathType Container)) {
    throw "CSP Studio repo not found: $Repo"
}
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "CSP Studio Python not found: $Python"
}

New-Item -ItemType Directory -Force -Path $Output | Out-Null
New-Item -ItemType Directory -Force -Path $ServiceDir | Out-Null

$env:CSP_OUTPUT_DIR = $Output
$env:CSP_STUDIO_DB = $Db

function Get-StudioHealth {
    try {
        return Invoke-RestMethod -Uri "$StudioUrl/api/health" -Method Get -TimeoutSec 2
    } catch {
        return $null
    }
}

function Start-WorkerIfNeeded {
    if ($SkipWorker) {
        Write-Host "Worker start skipped (-SkipWorker)."
        return
    }

    $worker = Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" -ErrorAction SilentlyContinue |
        Where-Object {
            $_.CommandLine -and
            $_.CommandLine.Contains("csp_studio.studio_worker") -and
            $_.CommandLine.Contains($Db)
        } |
        Select-Object -First 1

    if ($null -ne $worker) {
        Write-Host "Studio Worker already running PID=$($worker.ProcessId)"
        return
    }

    $scheduled = Get-ScheduledTask -TaskName "CSP Studio Worker" -ErrorAction SilentlyContinue
    $watchdog = Get-ScheduledTask -TaskName "CSP Studio Worker Watchdog" -ErrorAction SilentlyContinue
    if ($null -ne $scheduled -and $null -ne $watchdog) {
        & (Join-Path $Repo "setup\start-studio-worker.ps1") -Db $Db
        return
    }

    $workerOut = Join-Path $ServiceDir "worker.out.log"
    $workerErr = Join-Path $ServiceDir "worker.err.log"
    $args = @(
        "-m", "csp_studio.studio_worker",
        "--db", $Db,
        "--output-root", $Output
    )
    $process = Start-Process -FilePath $Python -ArgumentList $args -WorkingDirectory $Repo -WindowStyle Hidden -RedirectStandardOutput $workerOut -RedirectStandardError $workerErr -PassThru
    Write-Warning "Scheduled Studio Worker is not installed; started direct worker PID=$($process.Id). Crash watchdog is unavailable until setup\install-studio-worker.ps1 is installed."
}

Start-WorkerIfNeeded

$health = Get-StudioHealth
if ($null -ne $health) {
    $reportedDb = [System.IO.Path]::GetFullPath([string]$health.db)
    $reportedOutput = [System.IO.Path]::GetFullPath([string]$health.output_root)
    if ($reportedDb -ne $Db -or $reportedOutput -ne $Output) {
        throw "Another CSP Studio is already listening on port $Port with different paths. DB=$reportedDb Output=$reportedOutput"
    }
    Write-Host "CSP Studio already running: $StudioUrl"
} else {
    $existing = Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" -ErrorAction SilentlyContinue |
        Where-Object {
            $_.CommandLine -and
            $_.CommandLine.Contains("csp_studio.web_app:app") -and
            $_.CommandLine.Contains("--port") -and
            $_.CommandLine.Contains([string]$Port)
        } |
        Select-Object -First 1
    if ($null -ne $existing) {
        throw "CSP Studio process PID=$($existing.ProcessId) exists for port $Port but health check failed. Stop or diagnose it before starting another instance."
    }

    $studioOut = Join-Path $ServiceDir "studio.out.log"
    $studioErr = Join-Path $ServiceDir "studio.err.log"
    $args = @(
        "-m", "uvicorn", "csp_studio.web_app:app",
        "--host", "127.0.0.1",
        "--port", [string]$Port,
        "--log-level", "info"
    )
    $process = Start-Process -FilePath $Python -ArgumentList $args -WorkingDirectory $Repo -WindowStyle Hidden -RedirectStandardOutput $studioOut -RedirectStandardError $studioErr -PassThru
    Write-Host "Started CSP Studio PID=$($process.Id)"

    $health = $null
    foreach ($attempt in 1..40) {
        Start-Sleep -Milliseconds 500
        $health = Get-StudioHealth
        if ($null -ne $health) { break }
        if ($process.HasExited) {
            throw "CSP Studio exited during startup. Check $studioErr"
        }
    }
    if ($null -eq $health) {
        throw "CSP Studio did not become healthy on $StudioUrl. Check $studioErr"
    }
}

Write-Host "Health: OK"
Write-Host "DB: $($health.db)"
Write-Host "Output: $($health.output_root)"
Write-Host "Studio: $StudioUrl/"

if (-not $NoBrowser) {
    Start-Process "$StudioUrl/"
}
