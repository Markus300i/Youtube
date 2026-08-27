param(
    [Parameter(Mandatory = $true)]
    [string]$Token,
    [string]$RepoUrl = "https://github.com/Markus300i/Youtube",
    [string]$RunnerRoot = "C:\CSP\actions-runner",
    [string]$RunnerName = "CSP-RTX4060Ti"
)

$ErrorActionPreference = 'Stop'

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Uruchom PowerShell jako Administrator — instalacja runnera jako usługi tego wymaga.'
}

New-Item -ItemType Directory -Force -Path $RunnerRoot | Out-Null

$configCmd = Join-Path $RunnerRoot 'config.cmd'
if (-not (Test-Path $configCmd)) {
    Write-Host 'Pobieram najnowszy GitHub Actions Runner for Windows x64...'
    $release = Invoke-RestMethod -Headers @{ 'User-Agent' = 'CSP-Automation' } -Uri 'https://api.github.com/repos/actions/runner/releases/latest'
    $asset = $release.assets | Where-Object { $_.name -like 'actions-runner-win-x64-*.zip' } | Select-Object -First 1
    if (-not $asset) {
        throw 'Nie znaleziono assetu actions-runner-win-x64 w najnowszym release.'
    }

    $zip = Join-Path $env:TEMP $asset.name
    Write-Host "Runner: $($release.tag_name)"
    Write-Host "Download: $($asset.browser_download_url)"
    Invoke-WebRequest -UseBasicParsing -Uri $asset.browser_download_url -OutFile $zip
    Expand-Archive -Path $zip -DestinationPath $RunnerRoot -Force
    Remove-Item $zip -Force
}

Push-Location $RunnerRoot
try {
    if (Test-Path '.runner') {
        Write-Host 'Runner jest już skonfigurowany — pomijam config.cmd.'
    } else {
        & .\config.cmd `
            --url $RepoUrl `
            --token $Token `
            --name $RunnerName `
            --labels 'csp' `
            --work '_work' `
            --unattended `
            --replace
        if ($LASTEXITCODE -ne 0) {
            throw 'config.cmd zakończył się błędem.'
        }
    }

    if (-not (Test-Path '.\svc.cmd')) {
        throw 'Brak svc.cmd w katalogu runnera.'
    }

    # svc.cmd install jest idempotentny tylko częściowo, więc sprawdzamy usługę.
    $service = Get-Service | Where-Object { $_.Name -like 'actions.runner.*' -and $_.PathName -like "*$RunnerRoot*" } | Select-Object -First 1
    if (-not $service) {
        & .\svc.cmd install
        if ($LASTEXITCODE -ne 0) {
            throw 'Nie udało się zainstalować usługi GitHub Runner.'
        }
    }

    & .\svc.cmd start
    if ($LASTEXITCODE -ne 0) {
        throw 'Nie udało się uruchomić usługi GitHub Runner.'
    }
} finally {
    Pop-Location
}

Write-Host ''
Write-Host "Runner '$RunnerName' został skonfigurowany dla $RepoUrl z etykietą csp."
Write-Host 'Token rejestracyjny nie jest zapisywany przez ten skrypt.'
