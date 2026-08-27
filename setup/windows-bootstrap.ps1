$ErrorActionPreference = 'Stop'

Write-Host '== CSP Automation v1 bootstrap =='

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw 'Python nie jest dostępny w PATH.'
}

if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    Write-Warning 'FFmpeg nie jest dostępny w PATH. Zainstaluj FFmpeg z obsługą NVENC.'
}

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw 'Git nie jest dostępny w PATH.'
}

python -m venv .venv
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt

New-Item -ItemType Directory -Force -Path output | Out-Null
New-Item -ItemType Directory -Force -Path assets\voice | Out-Null
New-Item -ItemType Directory -Force -Path workflows\comfyui | Out-Null

Write-Host ''
Write-Host 'Bootstrap zakończony.'
Write-Host 'Następne kroki:'
Write-Host '1. Zainstaluj/uruchom ComfyUI na http://127.0.0.1:8188'
Write-Host '2. Dodaj workflow API modelu do workflows/comfyui/'
Write-Host '3. Ustaw node IDs w config/models.yaml'
Write-Host '4. Dodaj GitHub self-hosted runner z etykietą csp'
