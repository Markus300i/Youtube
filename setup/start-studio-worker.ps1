param(
    [string]$TaskName = "CSP Studio Worker",
    [string]$WatchdogTaskName = "CSP Studio Worker Watchdog",
    [string]$Db = "C:\CSP\output\csp-studio.db"
)

$ErrorActionPreference = "Stop"

$Db = [System.IO.Path]::GetFullPath($Db)
$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
Get-ScheduledTask -TaskName $WatchdogTaskName -ErrorAction Stop | Out-Null

Enable-ScheduledTask -TaskName $WatchdogTaskName | Out-Null
Enable-ScheduledTask -TaskName $TaskName | Out-Null

$existing = Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" -ErrorAction SilentlyContinue |
    Where-Object {
        $_.CommandLine -and
        $_.CommandLine.Contains("csp_studio.studio_worker") -and
        $_.CommandLine.Contains($Db)
    } |
    Select-Object -First 1

if ($null -eq $existing) {
    Start-ScheduledTask -InputObject $task
    Start-Sleep -Milliseconds 500
    Write-Host "Started scheduled task: $TaskName"
} else {
    Write-Host "Worker process already running PID=$($existing.ProcessId); scheduled task start skipped"
}

$info = Get-ScheduledTaskInfo -TaskName $TaskName
Write-Host "Watchdog enabled: $WatchdogTaskName"
Write-Host "State: $((Get-ScheduledTask -TaskName $TaskName).State)"
Write-Host "LastTaskResult: $($info.LastTaskResult)"
