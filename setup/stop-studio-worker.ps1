param(
    [string]$TaskName = "CSP Studio Worker",
    [string]$WatchdogTaskName = "CSP Studio Worker Watchdog",
    [string]$Db = "C:\CSP\output\csp-studio.db"
)

$ErrorActionPreference = "Stop"

$Db = [System.IO.Path]::GetFullPath($Db)
$watchdog = Get-ScheduledTask -TaskName $WatchdogTaskName -ErrorAction SilentlyContinue
if ($null -ne $watchdog) {
    Disable-ScheduledTask -TaskName $WatchdogTaskName | Out-Null
    if ($watchdog.State -eq "Running") {
        Stop-ScheduledTask -InputObject $watchdog
        Start-Sleep -Milliseconds 300
    }
}

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
if ($task.State -eq "Running") {
    Stop-ScheduledTask -InputObject $task
    Start-Sleep -Milliseconds 500
}

$workers = Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" -ErrorAction SilentlyContinue |
    Where-Object {
        $_.CommandLine -and
        $_.CommandLine.Contains("csp_studio.studio_worker") -and
        $_.CommandLine.Contains($Db)
    }

foreach ($worker in $workers) {
    Stop-Process -Id $worker.ProcessId -Force -ErrorAction SilentlyContinue
    Write-Host "Stopped worker process PID=$($worker.ProcessId)"
}

Write-Host "Stopped scheduled task/process: $TaskName"
Write-Host "Watchdog disabled: $WatchdogTaskName"
Write-Host "State: $((Get-ScheduledTask -TaskName $TaskName).State)"
