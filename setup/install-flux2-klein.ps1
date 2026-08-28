param(
    [string]$ComfyUIPath = 'C:\ComfyUI',
    [string]$HfToken = $(if ($env:HF_TOKEN) { $env:HF_TOKEN } else { '' })
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path $ComfyUIPath)) {
    throw ('ComfyUI path not found: ' + $ComfyUIPath)
}

$resolvedRoot = [System.IO.Path]::GetFullPath($ComfyUIPath)
$comfyRoot = $resolvedRoot

if (-not (Test-Path (Join-Path $comfyRoot 'main.py'))) {
    $nested = Join-Path $resolvedRoot 'ComfyUI'
    if (Test-Path (Join-Path $nested 'main.py')) {
        $comfyRoot = $nested
    }
    else {
        throw ('main.py not found in ' + $resolvedRoot + ' or ' + $nested)
    }
}

$modelsRoot = Join-Path $comfyRoot 'models'
Write-Host ('ComfyUI root: ' + $comfyRoot)
Write-Host ('Models root:  ' + $modelsRoot)

$downloads = @(
    @{
        Name = 'FLUX.2 Klein Base 4B FP8'
        Url = 'https://huggingface.co/black-forest-labs/FLUX.2-klein-base-4b-fp8/resolve/main/flux-2-klein-base-4b-fp8.safetensors'
        Target = Join-Path $modelsRoot 'diffusion_models\flux-2-klein-base-4b-fp8.safetensors'
    },
    @{
        Name = 'Qwen3 4B text encoder'
        Url = 'https://huggingface.co/Comfy-Org/z_image_turbo/resolve/main/split_files/text_encoders/qwen_3_4b.safetensors'
        Target = Join-Path $modelsRoot 'text_encoders\qwen_3_4b.safetensors'
    },
    @{
        Name = 'FLUX.2 VAE'
        Url = 'https://huggingface.co/Comfy-Org/flux2-dev/resolve/main/split_files/vae/flux2-vae.safetensors'
        Target = Join-Path $modelsRoot 'vae\flux2-vae.safetensors'
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

    $curlArgs = @(
        '-L',
        '--fail',
        '--retry', '5',
        '--retry-delay', '5',
        '-C', '-',
        '-o', $part
    )

    if ($HfToken) {
        $curlArgs += @('-H', ('Authorization: Bearer ' + $HfToken))
    }

    $curlArgs += $item.Url
    & curl.exe @curlArgs

    if ($LASTEXITCODE -ne 0) {
        if (-not $HfToken -and $item.Name -like 'FLUX.2 Klein*') {
            throw 'FLUX.2 download failed. If Hugging Face requires access, set HF_TOKEN and run this script again.'
        }
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
Write-Host 'FLUX.2 Klein edit models are ready.'
Write-Host 'Restart ComfyUI before running FLUX.2 image edits.'
