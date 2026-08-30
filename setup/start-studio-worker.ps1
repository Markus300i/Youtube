param(
    [string]$TaskName = "CSP Studio Worker",
    [string]$WatchdogTaskName = "CSP Studio Worker Watchdog"
)

$ErrorActionPreference = "Stop"

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
$watchdog = Get-ScheduledTask -TaskName $WatchdogTaskName -ErrorAction Stop

Enable-ScheduledTask -TaskName $WatchdogTaskName | Out-Null
Enable-ScheduledTask -TaskName $TaskName | Out-Null
Start-ScheduledTask -InputObject $task
Start-Sleep -Milliseconds 500
$info = Get-ScheduledTaskInfo -TaskName $TaskName

Write-Host "Started scheduled task: $TaskName"
Write-Host "Watchdog enabled: $WatchdogTaskName"
Write-Host "State: $((Get-ScheduledTask -TaskName $TaskName).State)"
Write-Host "LastTaskResult: $($info.LastTaskResult)"
