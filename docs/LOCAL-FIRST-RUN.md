# CSP Studio — lokalne uruchomienie na Windows

Ta instrukcja dotyczy aktualnej aplikacji **CSP Studio** na komputerze produkcyjnym z RTX 4060 Ti 8 GB.

Canonical lokalne ścieżki:

```text
Repo:        C:\CSP\Youtube
Python:      C:\CSP\venv\Scripts\python.exe
Output:      C:\CSP\output
SQLite:      C:\CSP\output\csp-studio.db
Studio URL:  http://127.0.0.1:8765/
ComfyUI:     http://127.0.0.1:8188/
```

## A. Jednorazowa instalacja

- [ ] Zainstaluj Git.
- [ ] Zainstaluj Python 3.11.
- [ ] Zainstaluj pełny FFmpeg z NVENC i libass.
- [ ] Zainstaluj ComfyUI.
- [ ] Sklonuj `Markus300i/Youtube` do `C:\CSP\Youtube`.
- [ ] Utwórz / przygotuj środowisko `C:\CSP\venv` zgodnie z wymaganiami projektu.
- [ ] Zainstaluj Z-Image / FLUX.2 Klein zgodnie z odpowiednimi skryptami w `setup\`.
- [ ] Opcjonalnie dodaj `C:\CSP\voice\narrator_reference.wav` dla Chatterbox.
- [ ] Skonfiguruj NVIDIA NIM przez `setup\configure-nim.ps1` — nie wpisuj klucza do repo ani SQLite.
- [ ] Zainstaluj trwałego Studio Workera przez `setup\install-studio-worker.ps1`.
- [ ] Uruchom preflight i usuń błędy środowiska przed produkcją.

## B. Normalne uruchomienie CSP Studio

W PowerShell:

```powershell
cd C:\CSP\Youtube
.\setup\start-csp-studio.ps1
```

Launcher:

- używa `C:\CSP\venv\Scripts\python.exe`,
- używa produkcyjnego `C:\CSP\output\csp-studio.db`,
- nie uruchamia drugiej kopii Studio, jeśli poprawna instancja już działa,
- nie uruchamia drugiego Workera dla tej samej bazy,
- uruchamia zainstalowany Worker + watchdog, jeśli są dostępne,
- w razie braku instalacji Workera może uruchomić bezpośredni worker z ostrzeżeniem,
- czeka na `/api/health`,
- otwiera `http://127.0.0.1:8765/`.

Bez automatycznego otwierania przeglądarki:

```powershell
.\setup\start-csp-studio.ps1 -NoBrowser
```

Uruchomienie samego GUI/API bez Workera:

```powershell
.\setup\start-csp-studio.ps1 -SkipWorker
```

Ta opcja jest przeznaczona do diagnostyki. Normalna produkcja powinna mieć Workera online.

## C. Zatrzymanie

Studio + Worker:

```powershell
.\setup\stop-csp-studio.ps1
```

Tylko Studio, Worker pozostaje aktywny:

```powershell
.\setup\stop-csp-studio.ps1 -KeepWorker
```

Zatrzymanie CSP Studio nie zatrzymuje ComfyUI.

## D. Szybki health check

```powershell
Invoke-RestMethod http://127.0.0.1:8765/api/health
Invoke-RestMethod http://127.0.0.1:8765/api/workers
Invoke-RestMethod http://127.0.0.1:8765/api/providers/nvidia-nim/status
```

Nie drukuj ani nie zapisuj `NVIDIA_API_KEY` podczas diagnostyki.

## E. Normalny workflow użytkownika

1. Otwórz CSP Studio.
2. Kliknij **+ Nowy Short**.
3. Spróbuj wygenerować draft przez Wizard V2 albo użyj ręcznego formularza.
4. Draft AI nie zapisuje projektu przed review.
5. Sprawdź narrację, 8 scen, prompty i Visual Bible.
6. Utwórz projekt.
7. Agent One pokaże deterministyczny następny krok.
8. Production Run / Task Engine wykonują dozwolone etapy przez Studio Workera.
9. Sceny wymagają ludzkiego review — program nie zatwierdza ich automatycznie.
10. OpenCut pozostaje warstwą timeline/editingu; CSP Studio nie buduje drugiego NLE.

Nieudany draft AI nie blokuje programu: można ponowić generację albo przejść do ręcznego Wizarda.

## F. Diagnostyka 8 GB VRAM

Pipeline wykonuje lokalne zadania GPU sekwencyjnie. Quick Regenerate używa szybszej ścieżki Z-Image, a Quality Regenerate może wykorzystywać bardziej kosztowną ścieżkę i referencje scen.

Jeżeli pojawi się CUDA OOM:

1. zamknij inne aplikacje wykorzystujące GPU,
2. sprawdź, czy nie działa drugi lokalny task GPU,
3. uruchom ponownie ComfyUI,
4. w razie potrzeby zmniejsz roboczą rozdzielczość modelu w `config/models.yaml`,
5. ponów tylko nieudane zadanie w Tasks.

Nie obniżaj finalnego `1080x1920` tylko z powodu roboczej rozdzielczości generatora.

## G. Gdzie szukać stanu i logów

```text
C:\CSP\output\csp-studio.db
C:\CSP\output\.studio-tasks\
C:\CSP\output\.studio-snapshots\
C:\CSP\output\.studio-service\
```

GUI Tasks pokazuje bezpiecznie ograniczone i redagowane logi zadań. Surowych sekretów nie należy kopiować do raportów ani commitów.

## H. Oczekiwane artefakty produkcji

Dla gotowego Shorta typowo:

```text
images\scene-01.png ... scene-08.png
audio\voice.wav
audio\tts-timings.json
subtitles.ass i/lub subtitles.srt
audio\final_mix.wav
opencut / interchange artifact
final.mp4
```

Agent One oraz pipeline freshness określają, czy artefakt jest aktualny. Samo istnienie starego pliku nie oznacza, że etap jest gotowy po zmianie sceny, obrazu lub audio.
