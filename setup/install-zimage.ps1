param(
    [string]$ComfyUIPath = "C:\ComfyUI"
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path $ComfyUIPath)) {
    throw "Nie znaleziono ComfyUI: $ComfyUIPath"
}

$modelsRoot = Join-Path $ComfyUIPath 'models'
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
        Write-Host "SKIP $($item.Name) ($sizeGB GB): $target"
        continue
    }

    $part = "$target.part"
    Write-Host ''
    Write-Host "Pobieram: $($item.Name)"
    Write-Host "Do:       $target"

    # curl -C - wznawia częściowy transfer. Dzięki .part przerwany download
    # nigdy nie jest traktowany przez ComfyUI jako gotowy model.
    & curl.exe -L --fail --retry 5 --retry-delay 5 -C - -o $part $item.Url
    if ($LASTEXITCODE -ne 0) {
        throw "Błąd pobierania $($item.Name)"
    }

    if (-not (Test-Path $part) -or (Get-Item $part).Length -lt 1MB) {
        throw "Pobrany plik wygląda na nieprawidłowy: $part"
    }

    Move-Item -Force $part $target
    $sizeGB = [math]::Round((Get-Item $target).Length / 1GB, 2)
    Write-Host "OK $($item.Name): $sizeGB GB"
}

Write-Host ''
Write-Host 'Modele Z-Image są gotowe.'
Write-Host 'Uruchom ponownie ComfyUI, jeśli było otwarte podczas instalacji.'
