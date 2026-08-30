param(
    [string]$TaskName = "CSP Studio Worker"
)

$ErrorActionPreference = "Stop"

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
Stop-ScheduledTask -InputObject $task
Start-Sleep -Milliseconds 500

Write-Host "Stopped scheduled task: $TaskName"
Write-Host "State: $((Get-ScheduledTask -TaskName $TaskName).State)"
