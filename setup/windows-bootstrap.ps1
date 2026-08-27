param(
    [string]$CspRoot = 'C:\CSP',
    [string]$ComfyUIPath = 'C:\ComfyUI',
    [switch]$InstallModels
)

$ErrorActionPreference = 'Stop'
Write-Host '== CSP Automation v1 bootstrap =='

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw 'Git is not available in PATH.'
}

if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    Write-Warning 'FFmpeg is not available in PATH. Install a full build with NVENC and libass.'
}

$basePython = $null

if (Get-Command py -ErrorAction SilentlyContinue) {
    try {
        $candidate = & py -3.11 -c 'import sys; print(sys.executable)'
        if ($LASTEXITCODE -eq 0 -and $candidate) {
            $basePython = ($candidate | Select-Object -First 1).Trim()
        }
    }
    catch {
        $basePython = $null
    }
}

if (-not $basePython -and (Get-Command python -ErrorAction SilentlyContinue)) {
    try {
        $version = & python -c 'import sys; print(str(sys.version_info.major) + "." + str(sys.version_info.minor))'
        if ($LASTEXITCODE -eq 0 -and ($version | Select-Object -First 1).Trim() -eq '3.11') {
            $candidate = & python -c 'import sys; print(sys.executable)'
            if ($LASTEXITCODE -eq 0 -and $candidate) {
                $basePython = ($candidate | Select-Object -First 1).Trim()
            }
        }
    }
    catch {
        $basePython = $null
    }
}

if (-not $basePython) {
    throw 'Python 3.11 is required. Install it and run bootstrap again.'
}

$venv = Join-Path $CspRoot 'venv'
$venvPython = Join-Path $venv 'Scripts\python.exe'
$outputDir = Join-Path $CspRoot 'output'
$voiceDir = Join-Path $CspRoot 'voice'

New-Item -ItemType Directory -Force -Path $CspRoot | Out-Null
New-Item -ItemType Directory -Force -Path $outputDir | Out-Null
New-Item -ItemType Directory -Force -Path $voiceDir | Out-Null

if (-not (Test-Path $venvPython)) {
    Write-Host ('Creating persistent environment: ' + $venv)
    & $basePython -m venv $venv
    if ($LASTEXITCODE -ne 0) {
        throw 'Failed to create CSP virtual environment.'
    }
}

& $venvPython -m pip install --upgrade pip setuptools wheel
if ($LASTEXITCODE -ne 0) {
    throw 'Failed to upgrade pip/setuptools/wheel.'
}

& $venvPython -m pip install torch==2.6.0 torchaudio==2.6.0 --index-url https://download.pytorch.org/whl/cu124
if ($LASTEXITCODE -ne 0) {
    throw 'Failed to install CUDA PyTorch 2.6.0.'
}

& $venvPython -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    throw 'Failed to install CSP requirements.'
}

Write-Host 'Checking CUDA for TTS...'
& $venvPython -c 'import torch; print("torch", torch.__version__, "cuda", torch.version.cuda, "available", torch.cuda.is_available())'
if ($LASTEXITCODE -ne 0) {
    throw 'PyTorch CUDA check could not run.'
}

& $venvPython -c 'import torch, sys; sys.exit(0 if torch.cuda.is_available() else 1)'
if ($LASTEXITCODE -ne 0) {
    throw 'CUDA is not available in the CSP environment.'
}

[Environment]::SetEnvironmentVariable('CSP_PYTHON', $venvPython, 'User')
[Environment]::SetEnvironmentVariable('CSP_OUTPUT_DIR', $outputDir, 'User')
[Environment]::SetEnvironmentVariable('CSP_COMFYUI_PATH', $ComfyUIPath, 'User')
[Environment]::SetEnvironmentVariable('CSP_COMFY_URL', 'http://127.0.0.1:8188', 'User')
[Environment]::SetEnvironmentVariable('CSP_VOICE_REFERENCE', (Join-Path $voiceDir 'narrator_reference.wav'), 'User')

if ($InstallModels) {
    if (-not (Test-Path $ComfyUIPath)) {
        throw ('ComfyUI path not found: ' + $ComfyUIPath)
    }

    $modelInstaller = Join-Path $PSScriptRoot 'install-zimage.ps1'
    & $modelInstaller -ComfyUIPath $ComfyUIPath
    if ($LASTEXITCODE -ne 0) {
        throw 'Z-Image model installation failed.'
    }
}

Write-Host ''
Write-Host 'Bootstrap complete.'
Write-Host ('Python CSP: ' + $venvPython)
Write-Host ('Output:     ' + $outputDir)
Write-Host ('Voice ref:  ' + (Join-Path $voiceDir 'narrator_reference.wav'))
Write-Host ('ComfyUI:    ' + $ComfyUIPath)
Write-Host ''
Write-Host 'Next steps:'
Write-Host '1. Start ComfyUI on http://127.0.0.1:8188.'
Write-Host ('2. Run: ' + $venvPython + ' scripts\preflight.py')
Write-Host '3. If needed, install the GitHub self-hosted runner.'
