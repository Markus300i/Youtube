# CSP Automation v1 — architektura

> Decyzje dotyczące wykorzystania zewnętrznych projektów są utrzymywane w [CSP_DONOR_MAP.md](CSP_DONOR_MAP.md). Przed budową dużego nowego modułu należy najpierw sprawdzić tę mapę.

```text
GitHub workflow_dispatch
        |
        v
Windows self-hosted runner [csp]
        |
        +--> validate_short.py
        |
        +--> ComfyUI API
        |      +--> Z-Image Turbo INT8/FP4
        |      +--> scene-01 ... scene-08.png
        |      +--> /free -> zwolnienie VRAM
        |
        +--> Chatterbox Multilingual V3
        |      +--> 8 segmentów głosu
        |      +--> voice.wav
        |      +--> tts-timings.json
        |
        +--> faster-whisper
        |      +--> word timestamps
        |      +--> subtitles.srt
        |      +--> subtitles.ass
        |
        +--> FFmpeg sound_design.py
        |      +--> roomtone / drone
        |      +--> silence before twist
        |      +--> impact @ scene 8
        |      +--> final_mix.wav
        |
        +--> FFmpeg + NVENC render.py
               +--> scene motion clips
               +--> exact TTS scene timing
               +--> ASS subtitles
               +--> 1080x1920 final.mp4
```

## Granice odpowiedzialności

### GitHub

Przechowuje tylko kod, workflow, konfigurację i definicje Shortów. Nie przechowuje modeli, próbki głosu ani materiałów produkcyjnych.

### ComfyUI

Jest wymiennym adapterem generowania obrazu. `scripts/generate_images.py` używa bindings z `config/models.yaml`, dzięki czemu późniejszy model może mieć inny graf bez zmiany TTS/renderera.

### Chatterbox

Jeden model jest ładowany raz na cały Short. Osiem segmentów używa tych samych conditionals referencji głosu. Segmentacja chroni przed problemem zbyt długiej generacji i daje dokładne czasy zmian scen.

### Whisper

Nie słucha finalnego miksu. Analizuje czysty `voice.wav`, dzięki czemu roomtone i impact nie pogarszają timestampów napisów.

### FFmpeg

Sound design i obraz są oddzielnymi etapami. Renderer wykorzystuje czasy scen z TTS, a nie arbitralne osiem równych odcinków.

## Strategia 8 GB VRAM

1. ComfyUI generuje tylko jedną scenę naraz.
2. Z-Image używa quantized diffusion model i text encoder.
3. Po scenie 8 ComfyUI dostaje `/free` z `unload_models=true`.
4. Dopiero później Chatterbox korzysta z CUDA.
5. Whisper uruchamia się po zakończeniu TTS.
6. FFmpeg używa NVENC, ale nie wymaga trzymania modelu AI w VRAM.

Dzięki temu modele nie konkurują jednocześnie o 8 GB pamięci karty.
