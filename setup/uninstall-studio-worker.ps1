param(
    [string]$TaskName = "CSP Studio Worker"
)

$ErrorActionPreference = "Stop"

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($null -eq $task) {
    Write-Host "Scheduled task not installed: $TaskName"
    exit 0
}

if ($task.State -eq "Running") {
    Stop-ScheduledTask -InputObject $task
    Start-Sleep -Milliseconds 500
}
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
Write-Host "Uninstalled scheduled task: $TaskName"
