param(
    [string]$Repo = "C:\CSP\Youtube",
    [string]$Python = "C:\CSP\venv\Scripts\python.exe",
    [string]$OutputRoot = "C:\CSP\output",
    [string]$Db = "C:\CSP\output\csp-studio.db",
    [string]$TaskName = "CSP Studio Worker"
)

$ErrorActionPreference = "Stop"

$Repo = [System.IO.Path]::GetFullPath($Repo)
$Python = [System.IO.Path]::GetFullPath($Python)
$OutputRoot = [System.IO.Path]::GetFullPath($OutputRoot)
$Db = [System.IO.Path]::GetFullPath($Db)

if (-not (Test-Path -LiteralPath $Repo -PathType Container)) {
    throw "Repo not found: $Repo"
}
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "Python not found: $Python"
}
if (-not (Test-Path -LiteralPath $OutputRoot -PathType Container)) {
    New-Item -ItemType Directory -Path $OutputRoot -Force | Out-Null
}

$arguments = "-m csp_studio.studio_worker --db `"$Db`" --output-root `"$OutputRoot`""
$action = New-ScheduledTaskAction -Execute $Python -Argument $arguments -WorkingDirectory $Repo
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew

$userId = if ($env:USERDOMAIN) { "$env:USERDOMAIN\$env:USERNAME" } else { $env:USERNAME }
$principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited

$task = New-ScheduledTask -Action $action -Trigger $trigger -Settings $settings -Principal $principal
Register-ScheduledTask -TaskName $TaskName -InputObject $task -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName

Write-Host "Installed and started scheduled task: $TaskName"
Write-Host "Repo:   $Repo"
Write-Host "Python: $Python"
Write-Host "DB:     $Db"
Write-Host "Output: $OutputRoot"
Write-Host "Status: Get-ScheduledTask -TaskName '$TaskName' | Get-ScheduledTaskInfo"
