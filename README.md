# Ciemna Strona Polski — Automation v1

Lokalny pipeline produkcji YouTube Shorts sterowany z GitHub Actions i wykonywany na komputerze z self-hosted runnerem.

## Założenia

- 32 GB RAM
- NVIDIA RTX 4060 Ti 8 GB
- Windows 11
- brak płatnych API w podstawowym pipeline
- ChatGPT pozostaje warstwą kreatywną do scenariuszy
- obrazy są generowane lokalnie przez ComfyUI
- TTS, transkrypcja i montaż są wykonywane lokalnie

## Pipeline

`short.yaml -> validate -> ComfyUI -> TTS -> Whisper -> FFmpeg -> final.mp4`

GitHub nie renderuje filmu w chmurze. Workflow używa etykiety `self-hosted`, więc cały ciężki rendering odbywa się na lokalnym komputerze.

## Struktura

- `shorts/` — definicje produkcyjne Shortów
- `config/` — konfiguracja modeli i renderera
- `scripts/` — kod pipeline
- `workflows/comfyui/` — eksporty workflow ComfyUI w formacie API
- `.github/workflows/` — orkiestracja GitHub Actions
- `output/` — lokalne pliki wynikowe; katalog ignorowany przez Git

## Szybki start

1. Sklonuj repo na komputer produkcyjny.
2. Uruchom `powershell -ExecutionPolicy Bypass -File setup/windows-bootstrap.ps1`.
3. Zainstaluj i skonfiguruj GitHub Actions self-hosted runner dla repozytorium.
4. Uruchom ComfyUI lokalnie na `http://127.0.0.1:8188`.
5. Wyeksportuj wybrany workflow ComfyUI w formacie API do `workflows/comfyui/`.
6. Uzupełnij `config/models.yaml` o ID węzłów promptu, seed i outputu.
7. Dodaj Short do `shorts/` na podstawie `shorts/_template.yaml`.
8. Uruchom workflow `Build CSP Short` z zakładki Actions.

## Obrazy

Pipeline nie jest przywiązany do jednego modelu. Konfiguracja przewiduje:

- `z-image-turbo` — preferowany tryb fotorealistyczny,
- `flux2-klein` — preferowany przy referencjach i spójności scen,
- dowolny przyszły workflow ComfyUI — bez zmiany kodu orkiestratora.

Dla RTX 4060 Ti 8 GB generujemy sceny sekwencyjnie. Pipeline nie próbuje trzymać jednocześnie modelu obrazu, TTS i Whisper w VRAM.

## Ważne

Historie kanału są fikcyjne. Pipeline nie powinien generować metadanych sugerujących, że opisane wydarzenia są prawdziwe.
