# Ciemna Strona Polski — Automation v1

Lokalny pipeline produkcji fikcyjnych YouTube Shorts sterowany przez GitHub Actions i wykonywany na komputerze z self-hosted runnerem.

## Cel v1

Po zatwierdzeniu pliku Shorta pipeline wykonuje lokalnie:

`YAML -> walidacja -> 8 obrazów -> polski lektor -> timestampy -> napisy -> sound design -> ruch scen -> NVENC -> final.mp4`

Podstawowa wersja nie wymaga płatnych API. Obliczenia wykonuje komputer produkcyjny.

## Sprzęt docelowy

- Windows 11
- 32 GB RAM
- NVIDIA RTX 4060 Ti 8 GB
- aktualny sterownik NVIDIA
- około 15–20 GB wolnego miejsca na modele i cache

## Stos

- **GitHub Actions self-hosted** — orkiestracja
- **ComfyUI** — lokalne generowanie obrazów
- **Z-Image Turbo** — domyślny generator obrazu
- **Chatterbox Multilingual V3** — polski TTS / voice cloning
- **faster-whisper** — timestampy słów
- **FFmpeg + NVENC + libass** — montaż, ruch, sound design i napisy

### Z-Image pod 8 GB VRAM

Pipeline używa wariantu zoptymalizowanego pod małą pamięć GPU:

- `z_image_turbo_int8_convrot.safetensors`
- `qwen_3_4b_fp4_mixed.safetensors`
- `ae.safetensors`

Sceny są generowane **sekwencyjnie, jedna po drugiej**. Po zakończeniu obrazów pipeline prosi ComfyUI o zwolnienie modeli z pamięci przed TTS/Whisper.

## Trwałe katalogi Windows

GitHub checkout jest traktowany wyłącznie jako kod i konfiguracja. Duże materiały nie trafiają do repozytorium.

Domyślnie:

```text
C:\CSP\venv\                     Python/TTS/Whisper
C:\CSP\output\                   obrazy, WAV, napisy, final.mp4
C:\CSP\voice\narrator_reference.wav
C:\CSP\actions-runner\           GitHub self-hosted runner
C:\ComfyUI\                       ComfyUI + modele
```

Dzięki temu checkout GitHub Actions nie kasuje gotowych renderów i nie trzeba instalować modeli przy każdym filmie.

## 1. Wymagania bazowe

Zainstaluj:

- Git
- Python **3.11**
- FFmpeg z obsługą `h264_nvenc` oraz filtra `ass/libass`
- ComfyUI
- aktualny sterownik NVIDIA

## 2. Bootstrap

W PowerShell uruchom z katalogu repo:

```powershell
powershell -ExecutionPolicy Bypass -File setup/windows-bootstrap.ps1 -ComfyUIPath "C:\ComfyUI" -InstallModels
```

Bootstrap:

1. tworzy trwałe `C:\CSP\venv`,
2. instaluje PyTorch CUDA,
3. instaluje Chatterbox Multilingual V3 oraz Whisper,
4. ustawia katalog output,
5. pobiera oficjalne modele Z-Image do ComfyUI,
6. ustawia zmienne środowiskowe użytkownika.

Pobieranie modeli używa plików `.part` i obsługuje wznowienie przerwanego transferu.

## 3. Referencja narratora

Opcjonalnie umieść czystą polską próbkę głosu tutaj:

```text
C:\CSP\voice\narrator_reference.wav
```

Najlepiej użyć około 8–12 sekund spokojnej mowy bez muzyki, pogłosu i efektów. Pipeline przygotowuje embedding referencji raz i wykorzystuje go do wszystkich ośmiu segmentów, ograniczając dryf głosu między scenami.

Używaj wyłącznie głosu, do którego masz odpowiednie prawa lub zgodę.

## 4. ComfyUI

ComfyUI musi odpowiadać lokalnie na:

```text
http://127.0.0.1:8188
```

Workflow API Z-Image jest już w repo:

```text
workflows/comfyui/z-image-turbo-api.json
```

Nie trzeba ręcznie eksportować node IDs ani instalować custom node'ów — workflow v1 używa core nodes ComfyUI.

## 5. Preflight

Po uruchomieniu ComfyUI wykonaj:

```powershell
C:\CSP\venv\Scripts\python.exe scripts\preflight.py
```

Preflight sprawdza m.in.:

- RTX/CUDA,
- PyTorch CUDA,
- Chatterbox,
- faster-whisper,
- FFmpeg `h264_nvenc`,
- FFmpeg `ass/libass`,
- dostępność katalogu output,
- ComfyUI API,
- wymagane core nodes,
- czy ComfyUI rzeczywiście widzi wszystkie trzy pliki modelu Z-Image,
- poprawność bindings workflow.

Pipeline nie powinien być uruchamiany produkcyjnie, dopóki preflight nie zakończy się `PREFLIGHT OK`.

## 6. GitHub self-hosted runner

W GitHub otwórz:

`Settings -> Actions -> Runners -> New self-hosted runner`

i skopiuj krótkotrwały token rejestracyjny. Następnie uruchom **PowerShell jako Administrator**:

```powershell
powershell -ExecutionPolicy Bypass -File setup/install-github-runner.ps1 -Token "TU_WKLEJ_TOKEN"
```

Skrypt sam pobiera najnowszy Windows x64 GitHub Actions Runner, rejestruje go dla repozytorium, dodaje etykietę `csp` i uruchamia jako usługę Windows.

Job oczekuje etykiet:

```text
self-hosted, windows, x64, csp
```

Token jest używany wyłącznie podczas rejestracji i skrypt go nie zapisuje.

Po bootstrapie zrestartuj runner, aby proces przejął zmienne środowiskowe.

## 7. Pierwszy smoke test

W repo znajduje się gotowy fikcyjny test produkcyjny:

```text
shorts/001-drzwi-0.yaml
```

Możesz najpierw sprawdzić sam plik:

```powershell
C:\CSP\venv\Scripts\python.exe scripts\validate_short.py shorts\001-drzwi-0.yaml
```

Następnie w GitHub:

`Actions -> Build CSP Short -> Run workflow`

Domyślnym plikiem jest `shorts/001-drzwi-0.yaml`.

## 8. Sound design v1

Sound design jest wykonywany lokalnie przez FFmpeg i nie wymaga biblioteki płatnych efektów.

Aktualnie obsługuje:

- subtelny proceduralny roomtone,
- niski drone,
- osobny profil dla wnętrza/piwnicy i lasu,
- rzeczywistą ciszę przed twistem,
- niski impact dokładnie na wejściu sceny 8.

Whisper analizuje **czysty `voice.wav`**, a efekty są dodawane później do `final_mix.wav`, więc ambience nie pogarsza synchronizacji napisów.

To jest bezpieczny fallback produkcyjny. W przyszłości proceduralne efekty można zastąpić własną biblioteką SFX bez zmiany reszty pipeline'u.

## 9. Wynik

Gotowy film pojawi się lokalnie, np.:

```text
C:\CSP\output\001-drzwi-0\final.mp4
```

W tym samym katalogu pozostają materiały diagnostyczne:

```text
images\scene-01.png ... scene-08.png
audio\voice.wav
audio\final_mix.wav
audio\segments\scene-01.wav ... scene-08.wav
audio\tts-timings.json
subtitles.srt
subtitles.ass
transcription-words.json
render-temp\
final.mp4
```

Jeżeli pipeline zostanie przerwany po kilku grafikach, następny run pomija istniejące `scene-XX.png` i kontynuuje od brakującej sceny.

## Format Shorta

Każdy film jest pojedynczym plikiem YAML w `shorts/`.

Ważne zasady v1:

- `fictional: true` jest obowiązkowe,
- dokładnie 8 scen,
- narracja 70–160 słów,
- `scenes[].text` musi odpowiadać zatwierdzonej narracji,
- maksymalnie 45 słów na pojedynczy segment TTS,
- napisy 2–5 słów,
- finalny format 1080×1920,
- dozwolone ruchy: `static`, `push_in`, `slow_push`, `pan_left`, `pan_right`.

## Modele obrazów

Warstwa obrazów jest oddzielona od reszty pipeline'u.

Aktualnie:

- `z-image-turbo` — aktywny i skonfigurowany pod 8 GB,
- `flux2-klein` — adapter zarezerwowany do późniejszego dodania trybu referencyjnego / większej spójności postaci.

Zmiana modelu nie wymaga przebudowy TTS, napisów ani renderera.

## Bezpieczeństwo repozytorium

Nie commituj:

- modeli `.safetensors`,
- wygenerowanych MP4/WAV/PNG,
- próbki głosu narratora,
- tokenów GitHub/Hugging Face,
- innych sekretów.

`.gitignore` wyklucza duże media i lokalne katalogi robocze.

## Zasada treści

Historie **Ciemnej Strony Polski są fikcyjne**. Pipeline ma wykorzystywać prawdziwe polskie realia jako scenografię, ale nie może przedstawiać fikcyjnej historii jako autentycznego wydarzenia.
