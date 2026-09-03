param(
    [string]$Repo = "C:\CSP\Youtube",
    [string]$Db = "C:\CSP\output\csp-studio.db",
    [int]$Port = 8765,
    [switch]$KeepWorker
)

$ErrorActionPreference = "Stop"

$Repo = [System.IO.Path]::GetFullPath($Repo)
$Db = [System.IO.Path]::GetFullPath($Db)

$studioProcesses = Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" -ErrorAction SilentlyContinue |
    Where-Object {
        $_.CommandLine -and
        $_.CommandLine.Contains("csp_studio.web_app:app") -and
        $_.CommandLine.Contains("--port") -and
        $_.CommandLine.Contains([string]$Port)
    }

foreach ($process in $studioProcesses) {
    Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
    Write-Host "Stopped CSP Studio PID=$($process.ProcessId)"
}
if (-not $studioProcesses) {
    Write-Host "No CSP Studio process found for port $Port"
}

if ($KeepWorker) {
    Write-Host "Studio Worker left running (-KeepWorker)."
    exit 0
}

$stopWorker = Join-Path $Repo "setup\stop-studio-worker.ps1"
$scheduled = Get-ScheduledTask -TaskName "CSP Studio Worker" -ErrorAction SilentlyContinue
if ($null -ne $scheduled -and (Test-Path -LiteralPath $stopWorker -PathType Leaf)) {
    & $stopWorker -Db $Db
    exit 0
}

$workers = Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" -ErrorAction SilentlyContinue |
    Where-Object {
        $_.CommandLine -and
        $_.CommandLine.Contains("csp_studio.studio_worker") -and
        $_.CommandLine.Contains($Db)
    }

foreach ($worker in $workers) {
    Stop-Process -Id $worker.ProcessId -Force -ErrorAction SilentlyContinue
    Write-Host "Stopped direct Studio Worker PID=$($worker.ProcessId)"
}
if (-not $workers) {
    Write-Host "No Studio Worker process found for DB $Db"
}
