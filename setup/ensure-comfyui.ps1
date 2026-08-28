param(
    [string]$ComfyUrl = $(if ($env:CSP_COMFY_URL) { $env:CSP_COMFY_URL } else { 'http://127.0.0.1:8188' }),
    [string]$ComfyUIPath = $(if ($env:CSP_COMFYUI_PATH) { $env:CSP_COMFYUI_PATH } else { 'C:\ComfyUI' }),
    [int]$TimeoutSeconds = 180
)

$ErrorActionPreference = 'Stop'

function Test-ComfyUI {
    param([string]$Url)
    try {
        $response = Invoke-RestMethod -Uri "$($Url.TrimEnd('/'))/system_stats" -TimeoutSec 5
        return $null -ne $response
    }
    catch {
        return $false
    }
}

if (Test-ComfyUI -Url $ComfyUrl) {
    Write-Host ('ComfyUI is already running: ' + $ComfyUrl)
    exit 0
}

$path = [System.IO.Path]::GetFullPath($ComfyUIPath)
$appPath = $path
if (-not (Test-Path (Join-Path $appPath 'main.py'))) {
    $nested = Join-Path $path 'ComfyUI'
    if (Test-Path (Join-Path $nested 'main.py')) {
        $appPath = $nested
    }
    else {
        throw ('main.py not found in ' + $path + ' or ' + $nested + '. Start ComfyUI manually or set CSP_COMFYUI_PATH.')
    }
}

$python = $env:CSP_COMFY_PYTHON
if (-not $python -or -not (Test-Path $python)) {
    $appParent = Split-Path $appPath -Parent
    $pathParent = Split-Path $path -Parent
    $candidates = @(
        (Join-Path $appPath '.venv\Scripts\python.exe'),
        (Join-Path $appParent '.venv\Scripts\python.exe'),
        (Join-Path $path '.venv\Scripts\python.exe'),
        (Join-Path $pathParent '.venv\Scripts\python.exe'),
        (Join-Path $appParent 'python_embeded\python.exe'),
        (Join-Path $path 'python_embeded\python.exe')
    )
    $python = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
}

if (-not $python -or -not (Test-Path $python)) {
    throw 'ComfyUI Python was not found. Set CSP_COMFY_PYTHON to the ComfyUI python.exe or start ComfyUI manually.'
}

$runtimeRoot = 'C:\CSP'
if ($env:CSP_OUTPUT_DIR) {
    $runtimeRoot = Split-Path $env:CSP_OUTPUT_DIR -Parent
}
$logDir = Join-Path $runtimeRoot 'logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$outLog = Join-Path $logDir 'comfyui.stdout.log'
$errLog = Join-Path $logDir 'comfyui.stderr.log'

$args = @(
    '-s',
    (Join-Path $appPath 'main.py'),
    '--listen', '127.0.0.1',
    '--port', '8188',
    '--disable-auto-launch',
    '--preview-method', 'none',
    '--enable-dynamic-vram',
    '--async-offload'
)

Write-Host ('Starting ComfyUI: ' + $python + ' ' + ($args -join ' '))
$process = Start-Process `
    -FilePath $python `
    -ArgumentList $args `
    -WorkingDirectory $appPath `
    -WindowStyle Hidden `
    -RedirectStandardOutput $outLog `
    -RedirectStandardError $errLog `
    -PassThru

$pidFile = Join-Path $runtimeRoot 'comfyui.pid'
Set-Content -Path $pidFile -Value $process.Id -Encoding ascii

$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
while ((Get-Date) -lt $deadline) {
    if ($process.HasExited) {
        $tail = ''
        if (Test-Path $errLog) {
            $tail = (Get-Content $errLog -Tail 40 -ErrorAction SilentlyContinue) -join "`n"
        }
        throw ("ComfyUI exited before the API became ready. Log:`n" + $tail)
    }
    if (Test-ComfyUI -Url $ComfyUrl) {
        Write-Host ('ComfyUI READY: ' + $ComfyUrl + ' (PID ' + $process.Id + ')')
        exit 0
    }
    Start-Sleep -Seconds 3
}

throw ('Timed out waiting for ComfyUI after ' + $TimeoutSeconds + ' seconds. Check ' + $errLog)
