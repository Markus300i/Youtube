param(
    [string]$RepoRoot = "C:\Users\pat30\Youtube",
    [string]$ComfyRoot = "D:\ComfyUI-Installs\CSP\ComfyUI\ComfyUI",
    [string]$CspPython = "C:\CSP\venv\Scripts\python.exe",
    [string]$Reference = "C:\CSP\output\001-drzwi-0\images\scene-01.png",
    [string]$ComfyUrl = "http://127.0.0.1:8188",
    [switch]$InstallModels,
    [switch]$ForceRestartComfy
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

function Assert-Exists([string]$Path, [string]$Label) {
    if (-not (Test-Path $Path)) {
        throw "${Label} not found: $Path"
    }
}

function Download-Model(
    [string]$Target,
    [string]$Url,
    [string]$Label
) {
    if (Test-Path $Target) {
        Write-Host "OK   $Label" -ForegroundColor Green
        return $false
    }

    if (-not $InstallModels) {
        throw "Missing ${Label}: $Target`nRun this script again with -InstallModels."
    }

    $dir = Split-Path -Parent $Target
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    $part = "$Target.part"

    Write-Host "GET  $Label" -ForegroundColor Cyan
    Write-Host "     $Target"
    & curl.exe -L --fail --retry 5 --retry-delay 3 -C - -o $part $Url
    if ($LASTEXITCODE -ne 0) {
        throw "Download failed: $Label"
    }
    Move-Item -Force $part $Target
    Write-Host "DONE $Label" -ForegroundColor Green
    return $true
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

    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep -Seconds 2
        if (Test-Comfy) {
            Write-Host "OK   ComfyUI API" -ForegroundColor Green
            return
        }
    }
    throw "ComfyUI did not start at $ComfyUrl"
}

Assert-Exists $RepoRoot "Repository"
Assert-Exists $ComfyRoot "ComfyUI root"
Assert-Exists $CspPython "CSP Python"
Assert-Exists $Reference "Scene 1 master"

$ipNode = Join-Path $ComfyRoot "custom_nodes\ComfyUI_IPAdapter_plus"
$auxNode = Join-Path $ComfyRoot "custom_nodes\comfyui_controlnet_aux"
Assert-Exists $ipNode "ComfyUI_IPAdapter_plus"
Assert-Exists $auxNode "comfyui_controlnet_aux"

$checkpoint = Join-Path $ComfyRoot "models\checkpoints\sd_xl_base_1.0.safetensors"
$clipVision = Join-Path $ComfyRoot "models\clip_vision\CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors"
$ipAdapter = Join-Path $ComfyRoot "models\ipadapter\ip-adapter-plus_sdxl_vit-h.safetensors"
$controlNet = Join-Path $ComfyRoot "models\controlnet\controlnet-canny-sdxl-1.0-small-fp16.safetensors"

$downloaded = $false
$downloaded = (Download-Model $checkpoint `
    "https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0/resolve/main/sd_xl_base_1.0.safetensors?download=true" `
    "SDXL Base 1.0") -or $downloaded
$downloaded = (Download-Model $clipVision `
    "https://huggingface.co/h94/IP-Adapter/resolve/main/models/image_encoder/model.safetensors?download=true" `
    "CLIP Vision ViT-H") -or $downloaded
$downloaded = (Download-Model $ipAdapter `
    "https://huggingface.co/h94/IP-Adapter/resolve/main/sdxl_models/ip-adapter-plus_sdxl_vit-h.safetensors?download=true" `
    "IP-Adapter Plus SDXL ViT-H") -or $downloaded
$downloaded = (Download-Model $controlNet `
    "https://huggingface.co/diffusers/controlnet-canny-sdxl-1.0-small/resolve/main/diffusion_pytorch_model.fp16.safetensors?download=true" `
    "SDXL Canny ControlNet small FP16") -or $downloaded

$wasRunning = Test-Comfy
if ($ForceRestartComfy -or ($downloaded -and $wasRunning)) {
    Stop-ComfyOnPort
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

$testScript = Join-Path $RepoRoot "scripts\test_sdxl_ipadapter_scene3.py"
$shortFile = Join-Path $RepoRoot "shorts\001-drzwi-0.yaml"
Assert-Exists $testScript "SDXL test script"
Assert-Exists $shortFile "Drzwi 0 YAML"

Push-Location $RepoRoot
try {
    & $CspPython $testScript $shortFile --reference $Reference
    if ($LASTEXITCODE -ne 0) {
        throw "SDXL/IP-Adapter test failed with exit code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}

$result = "C:\CSP\output\001-drzwi-0\compare\scene-03\sdxl-ipadapter-controlnet.png"
Write-Host ""
Write-Host "READY" -ForegroundColor Green
Write-Host $result
if (Test-Path $result) {
    Start-Process $result
}
