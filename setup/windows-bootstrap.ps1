param(
    [string]$CspRoot = "C:\CSP",
    [string]$ComfyUIPath = "C:\ComfyUI",
    [switch]$InstallModels
)

$ErrorActionPreference = 'Stop'
Write-Host '== CSP Automation v1 bootstrap =='

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw 'Git nie jest dostępny w PATH.'
}
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    Write-Warning 'FFmpeg nie jest dostępny w PATH. Zainstaluj pełny build z NVENC i libass.'
}

$basePython = $null
if (Get-Command py -ErrorAction SilentlyContinue) {
    try {
        $basePython = (& py -3.11 -c "import sys; print(sys.executable)").Trim()
    } catch {}
}
if (-not $basePython -and (Get-Command python -ErrorAction SilentlyContinue)) {
    $version = (& python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')").Trim()
    if ($version -eq '3.11') {
        $basePython = (& python -c "import sys; print(sys.executable)").Trim()
    }
}
if (-not $basePython) {
    throw 'Wymagany jest Python 3.11. Zainstaluj go i uruchom bootstrap ponownie.'
}

$venv = Join-Path $CspRoot 'venv'
$venvPython = Join-Path $venv 'Scripts\python.exe'
$outputDir = Join-Path $CspRoot 'output'
$voiceDir = Join-Path $CspRoot 'voice'

New-Item -ItemType Directory -Force -Path $CspRoot, $outputDir, $voiceDir | Out-Null

if (-not (Test-Path $venvPython)) {
    Write-Host "Tworzę trwałe środowisko: $venv"
    & $basePython -m venv $venv
}

& $venvPython -m pip install --upgrade pip setuptools wheel

# Chatterbox 0.1.7 wymaga torch/torchaudio 2.6.0. Instalujemy wariant CUDA,
# żeby pip nie wybrał przypadkiem buildu CPU na Windows.
& $venvPython -m pip install torch==2.6.0 torchaudio==2.6.0 --index-url https://download.pytorch.org/whl/cu124
& $venvPython -m pip install -r requirements.txt

Write-Host 'Sprawdzam CUDA dla TTS...'
& $venvPython -c "import torch; print('torch', torch.__version__, 'cuda', torch.version.cuda, 'available', torch.cuda.is_available()); assert torch.cuda.is_available(), 'CUDA niedostępna w środowisku CSP'"

[Environment]::SetEnvironmentVariable('CSP_PYTHON', $venvPython, 'User')
[Environment]::SetEnvironmentVariable('CSP_OUTPUT_DIR', $outputDir, 'User')
[Environment]::SetEnvironmentVariable('CSP_COMFYUI_PATH', $ComfyUIPath, 'User')
[Environment]::SetEnvironmentVariable('CSP_COMFY_URL', 'http://127.0.0.1:8188', 'User')
[Environment]::SetEnvironmentVariable('CSP_VOICE_REFERENCE', (Join-Path $voiceDir 'narrator_reference.wav'), 'User')

if ($InstallModels) {
    if (-not (Test-Path $ComfyUIPath)) {
        throw "Nie znaleziono ComfyUI pod $ComfyUIPath. Zainstaluj ComfyUI lub podaj poprawny -ComfyUIPath."
    }
    & "$PSScriptRoot\install-zimage.ps1" -ComfyUIPath $ComfyUIPath
}

Write-Host ''
Write-Host 'Bootstrap zakończony.'
Write-Host "Python CSP: $venvPython"
Write-Host "Output:     $outputDir"
Write-Host "Voice ref:  $(Join-Path $voiceDir 'narrator_reference.wav')"
Write-Host "ComfyUI:    $ComfyUIPath"
Write-Host ''
Write-Host 'Następne kroki:'
Write-Host '1. Uruchom ComfyUI na http://127.0.0.1:8188.'
Write-Host "2. Uruchom: $venvPython scripts\preflight.py"
Write-Host '3. Jeżeli nie masz runnera, pobierz token z Settings -> Actions -> Runners i uruchom setup\install-github-runner.ps1.'
Write-Host '4. Po instalacji/resecie zmiennych zrestartuj usługę GitHub Runner.'
