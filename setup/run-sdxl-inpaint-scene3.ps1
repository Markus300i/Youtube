param(
    [string]$RepoRoot = "C:\Users\pat30\Youtube",
    [string]$ComfyRoot = "D:\ComfyUI-Installs\CSP\ComfyUI\ComfyUI",
    [string]$CspPython = "C:\CSP\venv\Scripts\python.exe",
    [string]$Reference = "C:\CSP\output\001-drzwi-0\images\scene-01.png",
    [string]$ComfyUrl = "http://127.0.0.1:8188",
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
        $conn = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
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

$checkpoint = Join-Path $ComfyRoot "models\checkpoints\sd_xl_base_1.0.safetensors"
Assert-Exists $checkpoint "SDXL Base 1.0 checkpoint"

$wasRunning = Test-Comfy
if ($ForceRestartComfy -and $wasRunning) {
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

$testScript = Join-Path $RepoRoot "scripts\test_sdxl_inpaint_scene3.py"
$shortFile = Join-Path $RepoRoot "shorts\001-drzwi-0.yaml"
Assert-Exists $testScript "SDXL inpaint test script"
Assert-Exists $shortFile "Drzwi 0 YAML"

$logDir = Join-Path $RepoRoot "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$logFile = Join-Path $logDir "sdxl-inpaint-scene3.log"

Push-Location $RepoRoot
try {
    Write-Host "RUN  pure SDXL inpaint scene 3 D/E/F" -ForegroundColor Cyan
    $oldErrorAction = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $pythonOutput = & $CspPython -u $testScript $shortFile --reference $Reference 2>&1
    $exitCode = $LASTEXITCODE
    $ErrorActionPreference = $oldErrorAction

    $pythonOutput | Tee-Object -FilePath $logFile

    if ($exitCode -ne 0) {
        Write-Host ""
        Write-Host "FAILED - full Python/ComfyUI error is above." -ForegroundColor Red
        Write-Host "LOG  $logFile" -ForegroundColor Yellow
        throw "SDXL inpaint test failed with exit code $exitCode. See $logFile"
    }
}
finally {
    Pop-Location
}

$result = "C:\CSP\output\001-drzwi-0\compare\scene-03\sdxl-inpaint-def\comparison-DEF.jpg"
Write-Host ""
Write-Host "READY" -ForegroundColor Green
Write-Host $result
if (Test-Path $result) {
    Start-Process $result
}
