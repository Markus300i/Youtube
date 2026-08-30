param(
    [string]$TaskName = "CSP Studio Worker",
    [string]$WatchdogTaskName = "CSP Studio Worker Watchdog"
)

$ErrorActionPreference = "Stop"

foreach ($name in @($WatchdogTaskName, $TaskName)) {
    $task = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    if ($null -eq $task) {
        Write-Host "Scheduled task not installed: $name"
        continue
    }
    if ($task.State -eq "Running") {
        Stop-ScheduledTask -InputObject $task
        Start-Sleep -Milliseconds 300
    }
    Unregister-ScheduledTask -TaskName $name -Confirm:$false
    Write-Host "Uninstalled scheduled task: $name"
}
