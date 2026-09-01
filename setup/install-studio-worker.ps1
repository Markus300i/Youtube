param(
    [string]$Repo = "C:\CSP\Youtube",
    [string]$Python = "C:\CSP\venv\Scripts\python.exe",
    [string]$OutputRoot = "C:\CSP\output",
    [string]$Db = "C:\CSP\output\csp-studio.db",
    [string]$TaskName = "CSP Studio Worker",
    [string]$WatchdogTaskName = "CSP Studio Worker Watchdog"
)

$ErrorActionPreference = "Stop"

$Repo = [System.IO.Path]::GetFullPath($Repo)
$Python = [System.IO.Path]::GetFullPath($Python)
$OutputRoot = [System.IO.Path]::GetFullPath($OutputRoot)
$Db = [System.IO.Path]::GetFullPath($Db)
$WatchdogScript = Join-Path $Repo "setup\watch-studio-worker.ps1"

if (-not (Test-Path -LiteralPath $Repo -PathType Container)) {
    throw "Repo not found: $Repo"
}
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "Python not found: $Python"
}
if (-not (Test-Path -LiteralPath $WatchdogScript -PathType Leaf)) {
    throw "Watchdog script not found: $WatchdogScript"
}
if (-not (Test-Path -LiteralPath $OutputRoot -PathType Container)) {
    New-Item -ItemType Directory -Path $OutputRoot -Force | Out-Null
}

foreach ($existingName in @($WatchdogTaskName, $TaskName)) {
    $existing = Get-ScheduledTask -TaskName $existingName -ErrorAction SilentlyContinue
    if ($null -ne $existing -and $existing.State -eq "Running") {
        Stop-ScheduledTask -InputObject $existing
        Start-Sleep -Milliseconds 300
    }
}

$arguments = "-m csp_studio.studio_worker --db `"$Db`" --output-root `"$OutputRoot`""
$action = New-ScheduledTaskAction -Execute $Python -Argument $arguments -WorkingDirectory $Repo
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew

$userId = if ($env:USERDOMAIN) { "$env:USERDOMAIN\$env:USERNAME" } else { $env:USERNAME }
$principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited

$task = New-ScheduledTask -Action $action -Trigger $trigger -Settings $settings -Principal $principal
Register-ScheduledTask -TaskName $TaskName -InputObject $task -Force | Out-Null

$watchdogArguments = "-NoProfile -ExecutionPolicy Bypass -File `"$WatchdogScript`" -TaskName `"$TaskName`" -Repo `"$Repo`" -Python `"$Python`" -OutputRoot `"$OutputRoot`" -Db `"$Db`""
$watchdogAction = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $watchdogArguments -WorkingDirectory $Repo
$watchdogTrigger = New-ScheduledTaskTrigger `
    -Once `
    -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 1) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
$watchdogSettings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 1) `
    -MultipleInstances IgnoreNew
$watchdog = New-ScheduledTask -Action $watchdogAction -Trigger $watchdogTrigger -Settings $watchdogSettings -Principal $principal
Register-ScheduledTask -TaskName $WatchdogTaskName -InputObject $watchdog -Force | Out-Null

Enable-ScheduledTask -TaskName $TaskName | Out-Null
Enable-ScheduledTask -TaskName $WatchdogTaskName | Out-Null
Start-ScheduledTask -TaskName $TaskName

Write-Host "Installed and started scheduled task: $TaskName"
Write-Host "Installed watchdog task: $WatchdogTaskName"
Write-Host "Repo:     $Repo"
Write-Host "Python:   $Python"
Write-Host "DB:       $Db"
Write-Host "Output:   $OutputRoot"
Write-Host "Watchdog: every 1 minute; detects worker process and launches a fresh process if missing"
Write-Host "Status:   Get-ScheduledTask -TaskName '$TaskName','$WatchdogTaskName'"
