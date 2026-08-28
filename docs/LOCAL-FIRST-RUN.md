# CSP Automation v1 — pierwszy lokalny run

Ta lista dotyczy pierwszego uruchomienia na komputerze produkcyjnym RTX 4060 Ti 8 GB.

## A. Jednorazowo

- [ ] Zainstaluj Git.
- [ ] Zainstaluj Python 3.11.
- [ ] Zainstaluj pełny FFmpeg z NVENC i libass.
- [ ] Zainstaluj ComfyUI.
- [ ] Sklonuj `Markus300i/Youtube` i przełącz się na `csp-automation-v1`.
- [ ] Uruchom `setup/windows-bootstrap.ps1 -ComfyUIPath "C:\ComfyUI" -InstallModels`.
- [ ] Opcjonalnie dodaj `C:\CSP\voice\narrator_reference.wav`.
- [ ] Uruchom ComfyUI na `127.0.0.1:8188`.
- [ ] Uruchom `C:\CSP\venv\Scripts\python.exe scripts\preflight.py` i uzyskaj `PREFLIGHT OK`.
- [ ] Dodaj GitHub self-hosted runner z etykietą `csp`.

## B. Smoke test bez GitHub Actions

Te polecenia można wykonać ręcznie w PowerShell, aby łatwo wskazać ewentualny etap awarii:

```powershell
$py = 'C:\CSP\venv\Scripts\python.exe'
$short = 'shorts\001-drzwi-0.yaml'
$env:CSP_OUTPUT_DIR = 'C:\CSP\output'
$env:CSP_COMFY_URL = 'http://127.0.0.1:8188'
$env:CSP_VOICE_REFERENCE = 'C:\CSP\voice\narrator_reference.wav'

& $py scripts\validate_short.py $short
& $py scripts\generate_images.py $short
& $py scripts\generate_tts.py $short
& $py scripts\transcribe.py $short
& $py scripts\sound_design.py $short
& $py scripts\render.py $short
```

Oczekiwany wynik:

```text
C:\CSP\output\001-drzwi-0\final.mp4
```

## C. Diagnostyka 8 GB VRAM

Pipeline generuje tylko jedną scenę naraz i po scenach zwalnia modele ComfyUI.

Jeżeli mimo quantyzacji pojawi się CUDA OOM:

1. zamknij inne aplikacje wykorzystujące GPU,
2. uruchom ponownie ComfyUI,
3. zmniejsz roboczą rozdzielczość `z-image-turbo` w `config/models.yaml` z `768x1344` do `704x1248`,
4. powtórz tylko `generate_images.py` — istniejące sceny zostaną pominięte.

Nie obniżaj finalnego `1080x1920`; jest ono niezależne od rozdzielczości generowania.

## D. Co zachować po teście

Do oceny jakości zachowaj:

- `scene-01.png` … `scene-08.png`,
- `voice.wav`,
- `tts-timings.json`,
- `subtitles.ass`,
- `final_mix.wav`,
- `final.mp4`.

Na podstawie pierwszego renderu kalibrujemy potem:

- parametry Chatterbox (`cfg_weight`, `exaggeration`),
- wysokość i rozmiar napisów,
- szybkość push/pan,
- poziom ambience i impactu,
- roboczą rozdzielczość Z-Image.
