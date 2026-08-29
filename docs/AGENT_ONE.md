# CSP Studio — Agent One

Agent One is the production operator for CSP. It does not replace deterministic checks with an LLM.

## Rule

```text
filesystem + SQLite + checkpoints
        ↓
deterministic readiness
        ↓
verified JSON state
        ↓
optional AI/NVIDIA NIM explanation
```

A provider can explain or prioritize verified state, but cannot convert a failed gate into `READY`.

## Checks

Agent One inspects:
- exactly 8 scenes,
- active image asset for every scene,
- physical existence of active image files,
- scene review status (`approved` / `render_ready`),
- `audio/voice.wav`,
- exact `audio/tts-timings.json` covering scenes 1–8,
- ASS or SRT captions,
- `audio/final_mix.wav`,
- Visual QA checkpoint,
- OpenCut interchange export,
- final MP4.

Visual QA and OpenCut export are advisory workflow gates; missing production media/review remains a hard blocker.

## Readiness levels

```text
assets_ready
production_ready
final_ready
```

`final_ready` requires all production artifacts plus approved scenes. It never depends on an LLM response.

## Next action

The deterministic operator selects one next action:

```text
fix_scene_plan
complete_images
generate_tts
generate_captions
sound_design
visual_qa
review_scenes
export_opencut
render_final
publish_review
```

Automatable actions can be sent into `TaskEngine` with `enqueue_next()`. An already queued/running stage is not duplicated.

## NIM integration

`AgentOne.explain(project_id, provider)` passes only the verified readiness report to a `ChatProvider`. With `NvidiaNimProvider` this becomes an optional AI operator explanation without giving NIM authority over readiness.

No API key is included in the report or task payload.

## CLI

Deterministic inspection:

```powershell
$py = "C:\CSP\venv\Scripts\python.exe"
$env:CSP_OUTPUT_DIR = "C:\CSP\output"
& $py -m csp_studio.agent_one 001
```

JSON state:

```powershell
& $py -m csp_studio.agent_one 001 --json
```

## Tests

```powershell
& $py -m unittest tests.test_csp_agent_one -v
```

Tests cover missing assets, readiness progression, Visual QA checkpoint behavior, scene review, idempotent task enqueue and provider explanation grounded in deterministic state.
