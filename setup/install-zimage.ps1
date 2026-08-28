param(
    [string]$ComfyUIPath = 'C:\ComfyUI'
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path $ComfyUIPath)) {
    throw ('ComfyUI path not found: ' + $ComfyUIPath)
}

# Support both layouts:
#   classic/portable: <root>\main.py + <root>\models
#   Comfy Desktop managed install: <install>\ComfyUI\main.py + <install>\ComfyUI\models
$resolvedRoot = [System.IO.Path]::GetFullPath($ComfyUIPath)
$comfyRoot = $resolvedRoot

if (-not (Test-Path (Join-Path $comfyRoot 'main.py'))) {
    $nested = Join-Path $resolvedRoot 'ComfyUI'
    if (Test-Path (Join-Path $nested 'main.py')) {
        $comfyRoot = $nested
    }
    else {
        throw ('main.py not found in ' + $resolvedRoot + ' or ' + $nested + '. Point ComfyUIPath to the ComfyUI installation root or its parent directory.')
    }
}

$modelsRoot = Join-Path $comfyRoot 'models'
Write-Host ('ComfyUI root: ' + $comfyRoot)
Write-Host ('Models root:  ' + $modelsRoot)

$downloads = @(
    @{
        Name = 'Z-Image Turbo INT8 ConvRot'
        Url = 'https://huggingface.co/Comfy-Org/z_image_turbo/resolve/main/split_files/diffusion_models/z_image_turbo_int8_convrot.safetensors'
        Target = Join-Path $modelsRoot 'diffusion_models\z_image_turbo_int8_convrot.safetensors'
    },
    @{
        Name = 'Qwen3 4B FP4 Mixed text encoder'
        Url = 'https://huggingface.co/Comfy-Org/z_image_turbo/resolve/main/split_files/text_encoders/qwen_3_4b_fp4_mixed.safetensors'
        Target = Join-Path $modelsRoot 'text_encoders\qwen_3_4b_fp4_mixed.safetensors'
    },
    @{
        Name = 'Z-Image VAE'
        Url = 'https://huggingface.co/Comfy-Org/z_image_turbo/resolve/main/split_files/vae/ae.safetensors'
        Target = Join-Path $modelsRoot 'vae\ae.safetensors'
    }
)

foreach ($item in $downloads) {
    $target = $item.Target
    $dir = Split-Path -Parent $target
    New-Item -ItemType Directory -Force -Path $dir | Out-Null

    if ((Test-Path $target) -and ((Get-Item $target).Length -gt 1MB)) {
        $sizeGB = [math]::Round((Get-Item $target).Length / 1GB, 2)
        Write-Host ('SKIP ' + $item.Name + ' (' + $sizeGB + ' GB): ' + $target)
        continue
    }

    $part = $target + '.part'
    Write-Host ''
    Write-Host ('Downloading: ' + $item.Name)
    Write-Host ('Target:      ' + $target)

    # curl -C - resumes an interrupted transfer. The .part suffix prevents
    # ComfyUI from treating an incomplete download as a valid model file.
    & curl.exe -L --fail --retry 5 --retry-delay 5 -C - -o $part $item.Url
    if ($LASTEXITCODE -ne 0) {
        throw ('Download failed: ' + $item.Name)
    }

    if (-not (Test-Path $part) -or (Get-Item $part).Length -lt 1MB) {
        throw ('Downloaded file looks invalid: ' + $part)
    }

    Move-Item -Force $part $target
    $sizeGB = [math]::Round((Get-Item $target).Length / 1GB, 2)
    Write-Host ('OK ' + $item.Name + ': ' + $sizeGB + ' GB')
}

Write-Host ''
Write-Host 'Z-Image models are ready.'
Write-Host ('Models directory: ' + $modelsRoot)
Write-Host 'Restart ComfyUI if it was running during model installation.'
