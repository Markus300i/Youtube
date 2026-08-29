# Dokumentacja CSP Automation / CSP Studio

## Fundament

- [Pierwszy lokalny run](LOCAL-FIRST-RUN.md)
- [Architektura](ARCHITECTURE.md)
- [Mapa repozytoriów donorów — TAKE / ADAPT / REJECT](CSP_DONOR_MAP.md)
- [Status implementacji](STATUS.md)

## CSP Studio

- [CSP Studio GUI MVP](CSP_STUDIO_GUI_MVP.md)
- [P1 — OpenCut Adapter + Task Engine](P1_OPENCUT_TASK_ENGINE.md)
- [Agent One](AGENT_ONE.md)
- [Visual QA](VISUAL_QA.md)
- [Universe Memory](UNIVERSE_MEMORY.md)

## NVIDIA NIM

- [NVIDIA NIM Provider — chat / vision / embeddings](NVIDIA_NIM_PROVIDER.md)
- [NVIDIA Visual NIM — media experiments](NIM_MEDIA_EXPERIMENTS.md)

### Granica stabilności

```text
STABLE / OWN
CSP Scene/Asset/Revision + Chatterbox + Whisper + FFmpeg + manual GPT Image

INTEGRATION LAYER
OpenCut interchange + Task Engine + Agent One

OPTIONAL AI PROVIDER
NVIDIA NIM chat / VLM / embeddings

EXPERIMENTAL
NVIDIA Visual NIM FLUX.2 / Wan2.2 media generation
```

Główna instrukcja instalacji i użycia znajduje się również w repozytoryjnym `README.md`.
