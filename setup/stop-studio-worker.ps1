param(
    [string]$TaskName = "CSP Studio Worker",
    [string]$WatchdogTaskName = "CSP Studio Worker Watchdog"
)

$ErrorActionPreference = "Stop"

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

Write-Host "Stopped scheduled task: $TaskName"
Write-Host "Watchdog disabled: $WatchdogTaskName"
Write-Host "State: $((Get-ScheduledTask -TaskName $TaskName).State)"
