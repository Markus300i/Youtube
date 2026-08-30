param(
    [string]$TaskName = "CSP Studio Worker"
)

$ErrorActionPreference = "Stop"

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($null -eq $task) {
    Write-Host "Worker task not installed: $TaskName"
    exit 0
}

if ($task.State -eq "Disabled") {
    Write-Host "Worker task disabled: $TaskName"
    exit 0
}

if ($task.State -eq "Ready") {
    Start-ScheduledTask -TaskName $TaskName
    Write-Host "Watchdog restarted worker task: $TaskName"
    exit 0
}

Write-Host "Watchdog no-op: $TaskName state=$($task.State)"
