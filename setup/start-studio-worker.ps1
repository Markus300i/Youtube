param(
    [string]$TaskName = "CSP Studio Worker"
)

$ErrorActionPreference = "Stop"

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
Start-ScheduledTask -InputObject $task
Start-Sleep -Milliseconds 500
$info = Get-ScheduledTaskInfo -InputObject $task

Write-Host "Started scheduled task: $TaskName"
Write-Host "State: $((Get-ScheduledTask -TaskName $TaskName).State)"
Write-Host "LastTaskResult: $($info.LastTaskResult)"
