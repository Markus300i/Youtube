param(
    [string]$TaskName = "CSP Studio Worker",
    [string]$Repo = "C:\CSP\Youtube",
    [string]$Python = "C:\CSP\venv\Scripts\python.exe",
    [string]$OutputRoot = "C:\CSP\output",
    [string]$Db = "C:\CSP\output\csp-studio.db",
    [int]$CooldownSeconds = 3
)

$ErrorActionPreference = "Stop"

$Repo = [System.IO.Path]::GetFullPath($Repo)
$Python = [System.IO.Path]::GetFullPath($Python)
$OutputRoot = [System.IO.Path]::GetFullPath($OutputRoot)
$Db = [System.IO.Path]::GetFullPath($Db)

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($null -eq $task) {
    Write-Host "Worker task not installed: $TaskName"
    exit 0
}

if ($task.State -eq "Disabled") {
    Write-Host "Worker task disabled: $TaskName"
    exit 0
}

$workerPattern = "csp_studio.studio_worker"
$existing = Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" -ErrorAction SilentlyContinue |
    Where-Object {
        $_.CommandLine -and
        $_.CommandLine.Contains($workerPattern) -and
        $_.CommandLine.Contains($Db)
    } |
    Select-Object -First 1

if ($null -ne $existing) {
    Write-Host "Watchdog no-op: worker process already running PID=$($existing.ProcessId)"
    exit 0
}

if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "Python not found: $Python"
}
if (-not (Test-Path -LiteralPath $Repo -PathType Container)) {
    throw "Repo not found: $Repo"
}
if (-not (Test-Path -LiteralPath $OutputRoot -PathType Container)) {
    New-Item -ItemType Directory -Path $OutputRoot -Force | Out-Null
}

$cooldown = [Math]::Max(0, $CooldownSeconds)
if ($cooldown -gt 0) {
    Start-Sleep -Seconds $cooldown
}

$logDir = Join-Path $OutputRoot ".studio-worker"
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$stdoutLog = Join-Path $logDir "watchdog-worker.out.log"
$stderrLog = Join-Path $logDir "watchdog-worker.err.log"
$arguments = @(
    "-m",
    "csp_studio.studio_worker",
    "--db",
    $Db,
    "--output-root",
    $OutputRoot
)

$process = Start-Process `
    -FilePath $Python `
    -ArgumentList $arguments `
    -WorkingDirectory $Repo `
    -RedirectStandardOutput $stdoutLog `
    -RedirectStandardError $stderrLog `
    -WindowStyle Hidden `
    -PassThru

Write-Host "Watchdog launched worker directly PID=$($process.Id)"
Write-Host "stdout: $stdoutLog"
Write-Host "stderr: $stderrLog"
