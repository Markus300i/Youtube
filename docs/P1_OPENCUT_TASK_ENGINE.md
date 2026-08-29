# CSP P1 — OpenCut Adapter Spike + Task Engine

## Status

Implemented on `feature/csp-opencut-task-engine`.

This phase deliberately does **not** build a CSP timeline editor and does **not** depend on OpenCut private project serialization.

## OpenCut Adapter Spike

Module: `csp_studio/opencut_adapter.py`

The exporter reads canonical Studio state from SQLite and production timing/artifacts from the project output directory.

Input:

```text
Studio SQLite
├── scenes
├── ShotPlan
└── active image assets

project output
├── audio/tts-timings.json
├── audio/final_mix.wav or voice.wav
├── subtitles.ass
└── subtitles.srt
```

Output:

```text
<project>/opencut/csp-opencut.json
```

Format version:

```text
csp-opencut-interchange/1
```

The contract contains:
- 1080×1920 canvas,
- 30 fps,
- exact TTS scene start/end/duration,
- ordered active image revisions,
- provider/source metadata,
- scene revision/status,
- Shot Director intent,
- editor-neutral motion intent,
- master audio/narrator reference,
- ASS/SRT caption references,
- materialization notes for OpenCut.

The current OpenCut rewrite announces Editor API, headless mode, MCP and plugin architecture but these interfaces are not yet treated as stable by CSP. The Classic model was used only to validate the conceptual mapping `TProject -> TScene -> SceneTracks -> ImageElement/AudioElement/TextTrack`.

### Local command

```powershell
$py = "C:\CSP\venv\Scripts\python.exe"
$env:CSP_OUTPUT_DIR = "C:\CSP\output"
& $py -m csp_studio.opencut_adapter 001
```

Expected output path for `Drzwi 0`:

```text
C:\CSP\output\001-drzwi-0\opencut\csp-opencut.json
```

The exporter fails rather than inventing data when an active scene image or exact TTS timing is missing.

## Task Engine

Module: `csp_studio/task_engine.py`

SQLite tables:

```text
studio_tasks
pipeline_checkpoints
```

Task states:

```text
queued
running
succeeded
failed
cancelled
```

Resource classes:

```text
cpu
gpu
io
network
```

Implemented behavior:
- durable task state,
- progress 0–100,
- worker claim,
- only one running GPU task at a time,
- `failed_stage`,
- error persistence,
- retry counter,
- result/payload JSON,
- cancel,
- stage/scene checkpoints,
- checkpoint artifact existence validation,
- atomic JSON writes via temp + flush + fsync + `os.replace`.

This is inspired by MoneyPrinterTurbo state/task-manager patterns but uses CSP SQLite instead of copying its memory/Redis architecture.

### CLI

List tasks:

```powershell
& $py -m csp_studio.task_engine list --project 001
```

Submit a task:

```powershell
& $py -m csp_studio.task_engine submit 001 render_preview --resource gpu
```

## Tests

```powershell
& $py -m unittest `
  tests.test_csp_opencut_adapter `
  tests.test_csp_task_engine -v
```

These tests cover ordered exact-timing interchange, missing active assets, task lifecycle, GPU serialization, failure/retry, checkpoints and atomic writes.

## Next P2

After local verification of P1:

1. expose task state and OpenCut export in the CSP Studio GUI/API,
2. create the first worker that wraps existing CSP pipeline stages using Task Engine checkpoints,
3. begin Agent One readiness gates,
4. add the provider layer (`NvidiaNimProvider`, later other LLM/VLM providers),
5. use NIM first for Agent One/Visual QA rather than replacing the working media pipeline.
