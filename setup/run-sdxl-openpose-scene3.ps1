param(
    [string]$RepoRoot = "C:\Users\pat30\Youtube",
    [string]$ComfyRoot = "D:\ComfyUI-Installs\CSP\ComfyUI\ComfyUI",
    [string]$CspPython = "C:\CSP\venv\Scripts\python.exe",
    [string]$Reference = "C:\CSP\output\001-drzwi-0\images\scene-01.png",
    [string]$ComfyUrl = "http://127.0.0.1:8188",
    [string]$CheckpointName = "RealVisXL_V5.0_fp16.safetensors",
    [switch]$InstallModel,
    [switch]$InstallCheckpoint,
    [switch]$ForceRestartComfy
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

function Assert-Exists([string]$Path, [string]$Label) {
    if (-not (Test-Path $Path)) {
        throw "${Label} not found: $Path"
    }
}

function Test-Comfy {
    try {
        $null = Invoke-RestMethod -Uri "$ComfyUrl/system_stats" -TimeoutSec 5
        return $true
    }
    catch {
        return $false
    }
}

function Stop-ComfyOnPort {
    try {
        $port = ([uri]$ComfyUrl).Port
        $conn = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($conn -and $conn.OwningProcess) {
            Write-Host "STOP ComfyUI PID $($conn.OwningProcess)" -ForegroundColor Yellow
            Stop-Process -Id $conn.OwningProcess -Force -ErrorAction Stop
            Start-Sleep -Seconds 2
        }
    }
    catch {
        Write-Warning "Could not stop existing ComfyUI process: $_"
    }
}

function Start-Comfy {
    $comfyPython = Join-Path $ComfyRoot ".venv\Scripts\python.exe"
    $main = Join-Path $ComfyRoot "main.py"
    Assert-Exists $comfyPython "ComfyUI Python"
    Assert-Exists $main "ComfyUI main.py"

    Write-Host "START ComfyUI --lowvram" -ForegroundColor Cyan
    Start-Process -FilePath $comfyPython `
        -ArgumentList @($main, "--listen", "127.0.0.1", "--port", "8188", "--lowvram") `
        -WorkingDirectory $ComfyRoot | Out-Null

    for ($i = 0; $i -lt 90; $i++) {
        Start-Sleep -Seconds 2
        if (Test-Comfy) {
            Write-Host "OK   ComfyUI API" -ForegroundColor Green
            return
        }
    }
    throw "ComfyUI did not start at $ComfyUrl"
}

function Ensure-OpenPoseModel {
    $target = Join-Path $ComfyRoot "models\controlnet\xinsir-controlnet-openpose-sdxl-1.0.safetensors"
    if (Test-Path $target) {
        Write-Host "OK   Xinsir SDXL OpenPose ControlNet" -ForegroundColor Green
        return $false
    }

    if (-not $InstallModel) {
        throw "Missing Xinsir SDXL OpenPose ControlNet: $target`nRun again with -InstallModel."
    }

    $dir = Split-Path -Parent $target
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    $part = "$target.part"
    $url = "https://huggingface.co/xinsir/controlnet-openpose-sdxl-1.0/resolve/main/diffusion_pytorch_model.safetensors?download=true"

    Write-Host "GET  Xinsir SDXL OpenPose ControlNet (~2.5 GB)" -ForegroundColor Cyan
    Write-Host "     $target"
    & curl.exe -L --fail --retry 5 --retry-delay 3 -C - -o $part $url
    if ($LASTEXITCODE -ne 0) {
        throw "Download failed: Xinsir SDXL OpenPose ControlNet"
    }
    Move-Item -Force $part $target
    Write-Host "DONE Xinsir SDXL OpenPose ControlNet" -ForegroundColor Green
    return $true
}

function Ensure-Checkpoint {
    $target = Join-Path $ComfyRoot ("models\checkpoints\" + $CheckpointName)
    if (Test-Path $target) {
        Write-Host "OK   RealVisXL V5.0 fp16 checkpoint" -ForegroundColor Green
        return $false
    }

    if (-not $InstallCheckpoint) {
        throw "Missing RealVisXL checkpoint: $target`nRun again with -InstallCheckpoint."
    }

    $dir = Split-Path -Parent $target
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    $part = "$target.part"
    $url = "https://huggingface.co/SG161222/RealVisXL_V5.0/resolve/main/RealVisXL_V5.0_fp16.safetensors?download=true"

    Write-Host "GET  RealVisXL V5.0 fp16 (~6.94 GB)" -ForegroundColor Cyan
    Write-Host "     $target"
    & curl.exe -L --fail --retry 5 --retry-delay 3 -C - -o $part $url
    if ($LASTEXITCODE -ne 0) {
        throw "Download failed: RealVisXL V5.0 fp16"
    }
    Move-Item -Force $part $target
    Write-Host "DONE RealVisXL V5.0 fp16" -ForegroundColor Green
    return $true
}

Assert-Exists $RepoRoot "Repository"
Assert-Exists $ComfyRoot "ComfyUI root"
Assert-Exists $CspPython "CSP Python"
Assert-Exists $Reference "Scene 1 master"

$openPoseDownloaded = Ensure-OpenPoseModel
$checkpointDownloaded = Ensure-Checkpoint
$downloaded = $openPoseDownloaded -or $checkpointDownloaded
$wasRunning = Test-Comfy

if ($ForceRestartComfy -or ($downloaded -and $wasRunning)) {
    if ($wasRunning) {
        Stop-ComfyOnPort
    }
    Start-Comfy
}
elseif (-not $wasRunning) {
    Start-Comfy
}
else {
    Write-Host "OK   ComfyUI API already running" -ForegroundColor Green
}

$env:CSP_COMFY_URL = $ComfyUrl
$env:CSP_OUTPUT_DIR = "C:\CSP\output"

$testScript = Join-Path $RepoRoot "scripts\test_sdxl_openpose_scene3.py"
$shortFile = Join-Path $RepoRoot "shorts\001-drzwi-0.yaml"
Assert-Exists $testScript "SDXL OpenPose test script"
Assert-Exists $shortFile "Drzwi 0 YAML"

$logDir = Join-Path $RepoRoot "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$logFile = Join-Path $logDir "sdxl-openpose-scene3.log"

Push-Location $RepoRoot
try {
    Write-Host "RUN  FINAL RealVisXL + OpenPose inpaint scene 3" -ForegroundColor Cyan
    Write-Host "     checkpoint=$CheckpointName | steps=40 | cfg=6.5 | CN=0.58 | end=0.55" -ForegroundColor DarkGray
    $oldErrorAction = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $pythonOutput = & $CspPython -u $testScript $shortFile `
        --reference $Reference `
        --checkpoint $CheckpointName `
        --steps 40 `
        --cfg 6.5 `
        --control-strength 0.58 `
        --control-end 0.55 2>&1
    $exitCode = $LASTEXITCODE
    $ErrorActionPreference = $oldErrorAction

    $pythonOutput | Tee-Object -FilePath $logFile

    if ($exitCode -ne 0) {
        Write-Host ""
        Write-Host "FAILED - full Python/ComfyUI error is above." -ForegroundColor Red
        Write-Host "LOG  $logFile" -ForegroundColor Yellow
        throw "Final RealVisXL/OpenPose test failed with exit code $exitCode. See $logFile"
    }
}
finally {
    Pop-Location
}

$result = "C:\CSP\output\001-drzwi-0\compare\scene-03\sdxl-openpose\comparison-openpose.jpg"
Write-Host ""
Write-Host "READY" -ForegroundColor Green
Write-Host $result
if (Test-Path $result) {
    Start-Process $result
}
