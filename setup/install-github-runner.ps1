param(
    [Parameter(Mandatory = $true)]
    [string]$Token,
    [string]$RepoUrl = 'https://github.com/Markus300i/Youtube',
    [string]$RunnerRoot = 'C:\CSP\actions-runner',
    [string]$RunnerName = 'CSP-RTX4060Ti'
)

$ErrorActionPreference = 'Stop'

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    throw 'Run PowerShell as Administrator. Installing the runner as a service requires admin rights.'
}

New-Item -ItemType Directory -Force -Path $RunnerRoot | Out-Null

$configCmd = Join-Path $RunnerRoot 'config.cmd'
if (-not (Test-Path $configCmd)) {
    Write-Host 'Downloading latest GitHub Actions Runner for Windows x64...'
    $release = Invoke-RestMethod -Headers @{ 'User-Agent' = 'CSP-Automation' } -Uri 'https://api.github.com/repos/actions/runner/releases/latest'
    $asset = $release.assets | Where-Object { $_.name -like 'actions-runner-win-x64-*.zip' } | Select-Object -First 1
    if (-not $asset) {
        throw 'Windows x64 runner asset was not found in the latest GitHub Actions Runner release.'
    }

    $zip = Join-Path $env:TEMP $asset.name
    Write-Host ('Runner: ' + $release.tag_name)
    Write-Host ('Download: ' + $asset.browser_download_url)
    Invoke-WebRequest -UseBasicParsing -Uri $asset.browser_download_url -OutFile $zip
    Expand-Archive -Path $zip -DestinationPath $RunnerRoot -Force
    Remove-Item $zip -Force
}

Push-Location $RunnerRoot
try {
    if (Test-Path '.runner') {
        Write-Host 'Runner is already configured. Skipping config.cmd.'
    }
    else {
        & .\config.cmd `
            --url $RepoUrl `
            --token $Token `
            --name $RunnerName `
            --labels 'csp' `
            --work '_work' `
            --unattended `
            --replace
        if ($LASTEXITCODE -ne 0) {
            throw 'config.cmd failed.'
        }
    }

    if (-not (Test-Path '.\svc.cmd')) {
        throw 'svc.cmd was not found in the runner directory.'
    }

    # Get-Service does not expose PathName. Win32_Service lets us verify that
    # an existing service really points to this runner directory.
    $escapedRoot = [Regex]::Escape($RunnerRoot)
    $service = Get-CimInstance Win32_Service |
        Where-Object { $_.Name -like 'actions.runner.*' -and $_.PathName -match $escapedRoot } |
        Select-Object -First 1

    if (-not $service) {
        & .\svc.cmd install
        if ($LASTEXITCODE -ne 0) {
            throw 'Failed to install the GitHub Actions Runner service.'
        }
    }

    & .\svc.cmd start
    if ($LASTEXITCODE -ne 0) {
        throw 'Failed to start the GitHub Actions Runner service.'
    }
}
finally {
    Pop-Location
}

Write-Host ''
Write-Host ("Runner '" + $RunnerName + "' is configured for " + $RepoUrl + ' with label csp.')
Write-Host 'The registration token is not stored by this script.'
