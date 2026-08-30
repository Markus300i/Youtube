# CSP Studio — Codex Handoff

Snapshot date: **2026-08-30**  
Repository: `Markus300i/Youtube`  
Active development branch: `feature/csp-studio-ops-dashboard`  
Active PR: **#11**  
PR base: `feature/csp-nim-media-experiments` (PR #10)  
Head at handoff creation: `c5462ed8a09a2289714ab0c821299268987796fa`

This document is the current operational handoff for continuing CSP Studio in Codex. Older module docs remain useful for design details, but some historical status files predate the latest real local tests and should not override the verified/unverified state recorded here.

---

## 1. Product goal

**CSP Studio** is a local-first control/AI/domain layer for producing fictional YouTube Shorts for **Ciemna Strona Polski**.

Channel content rules:

- stories are original and fictional,
- Polish setting and natural Polish narration,
- documentary-thriller tone rather than exaggerated horror,
- hook immediately, one clear idea, rising tension, twist, closing question,
- no gore by default,
- never present fictional stories as real events.

The system should reduce manual production work while keeping scene review and quality decisions under user control.

---

## 2. Architectural decision: CSP Studio + OpenCut

The project is deliberately split:

### CSP Studio owns WHAT

- project/story/script domain,
- scenes, prompts and structured ShotPlan,
- Shot Director and visual-continuity intent,
- asset revisions and approvals,
- manual/imported/generated images,
- Task Engine,
- pipeline checkpoints and artifact freshness,
- Agent One deterministic readiness,
- provider integrations,
- Visual QA,
- Universe Memory,
- production dashboard and review state.

### OpenCut owns HOW

- timeline/tracks,
- clip order/duration/trim,
- keyframes/motion,
- transitions,
- playback,
- narrator/SFX/music/text tracks,
- eventual supported headless/editor materialization/export.

**Do not build a second full video editor inside CSP Studio.**

CSP currently emits the versioned interchange format:

```text
csp-opencut-interchange/1
```

See:

- `docs/P1_OPENCUT_TASK_ENGINE.md`
- `csp_studio/opencut_adapter.py`
- `docs/CSP_DONOR_MAP.md`

---

## 3. Local production environment

Target machine:

```text
OS:          Windows
Repo:        C:\Users\pat30\Youtube
Python:      C:\CSP\venv\Scripts\python.exe
Output root: C:\CSP\output
Studio DB:   C:\CSP\output\csp-studio.db
Studio URL:  http://127.0.0.1:8765/
Hardware:    RTX 4060 Ti 8 GB
RAM:         32 GB
```

Proof-of-concept project:

```text
Project ID: 001
Title:      Drzwi 0
YAML:       shorts/001-drzwi-0.yaml
Output:     C:\CSP\output\001-drzwi-0
Images:     C:\CSP\output\001-drzwi-0\images\scene-01.png ... scene-08.png
Final:      C:\CSP\output\001-drzwi-0\final.mp4
```

Typical shell setup:

```powershell
cd C:\Users\pat30\Youtube
$py = "C:\CSP\venv\Scripts\python.exe"
$env:CSP_OUTPUT_DIR = "C:\CSP\output"
```

Run Studio:

```powershell
& $py -m uvicorn csp_studio.web_app:app `
    --host 127.0.0.1 `
    --port 8765 `
    --log-level info
```

Health check:

```powershell
Invoke-RestMethod http://127.0.0.1:8765/api/health
```

A real local health call was confirmed to return `ok: True` and point to `C:\CSP\output\csp-studio.db` / `C:\CSP\output`.

---

## 4. Current production stack

### Images

Production paths:

- manual GPT Image/browser output: quality-first reviewed source,
- local **Z-Image Turbo**: draft/fallback and Quick Regenerate,
- local **FLUX.2 Klein edit**: controlled quality/reference edits,
- NVIDIA media NIM: experimental candidates only.

Relevant config:

```text
config/models.yaml
```

Current Z-Image config is designed for 8 GB VRAM and uses quantized components. Quality scenes may retain scene-level `render.mode` such as `crop` or `flux_edit` with `reference_scene`.

### TTS

```text
Chatterbox Multilingual V3
language: pl
device: cuda
```

Expected artifacts:

```text
audio/voice.wav
audio/tts-timings.json
```

### Captions

faster-whisper over clean narrator audio, not the final mix.

Expected artifacts:

```text
subtitles.ass and/or subtitles.srt
```

### Sound

Procedural sound design currently produces:

```text
audio/final_mix.wav
```

### Render

Current fallback/final renderer is FFmpeg/NVENC, 1080x1920 vertical.

OpenCut integration is additive and should gradually replace hand-built editing behavior where OpenCut exposes supported APIs.

---

## 5. Stacked PR chain

The current work is intentionally stacked. Do not flatten or merge without explicit user approval.

| PR | Branch | Base | Purpose |
|---|---|---|---|
| #1 | `csp-automation-v1` | `main` | Base local Shorts pipeline |
| #2 | `feature/csp-v1-1-quality-fixes` | `csp-automation-v1` | safer subtitles + manual image resume |
| #3 | `feature/csp-studio-foundation` | #2 | SQLite domain, assets, revisions, Scene Operations |
| #4 | `feature/csp-studio-gui-mvp` | #3 | Scene Editor V2 + Shot Director GUI |
| #5 | `feature/csp-opencut-task-engine` | #4 | OpenCut interchange + persistent Task Engine |
| #6 | `feature/csp-provider-nim` | #5 | provider-neutral NIM chat/vision/embeddings |
| #7 | `feature/csp-agent-one` | #6 | deterministic readiness/operator |
| #8 | `feature/csp-visual-qa` | #7 | real image Visual QA |
| #9 | `feature/csp-universe-memory` | #8 | canonical lore + embeddings/search |
| #10 | `feature/csp-nim-media-experiments` | #9 | experimental NVIDIA FLUX/Wan media contracts |
| #11 | `feature/csp-studio-ops-dashboard` | #10 | operations dashboard, executable actions, regenerate modes, pipeline freshness |

At handoff time PR #11 is open, not merged, not draft, and mergeable.

---

## 6. Core Studio modules

Important code entry points:

```text
csp_studio/models.py             domain models
csp_studio/store.py              SQLite persistence
csp_studio/asset_manager.py      active assets/revisions/statuses
csp_studio/scene_ops.py          scene image replacement/approval/history
csp_studio/shot_director.py      deterministic shot structure audit
csp_studio/task_engine.py        durable task/checkpoint state
csp_studio/task_runner.py        allow-listed executable pipeline worker
csp_studio/pipeline_state.py     freshness/invalidation graph
csp_studio/agent_one.py          deterministic readiness + next action
csp_studio/visual_qa.py          NIM/provider-based visual review
csp_studio/universe_memory.py    canonical lore + derived embeddings
csp_studio/agent_memory.py       advisory memory context for Agent One
csp_studio/opencut_adapter.py    CSP -> OpenCut interchange
csp_studio/action_api.py         Quick Regenerate + Manual Actions API
csp_studio/ops_api.py            dashboard/task API
csp_studio/web_app.py            FastAPI Studio application
csp_studio/web/app.js            main zero-build UI
csp_studio/web/actions.js        Quick/Quality + Manual Actions UI layer
```

---

## 7. Agent One invariant

Agent One is deterministic first:

```text
filesystem + SQLite + checkpoints
        -> readiness facts
        -> next action
        -> optional AI explanation
```

An LLM/NIM can explain state but cannot override it.

Agent One currently checks:

- exactly 8 scenes,
- active image asset/file for every scene,
- scene approval/review,
- TTS voice + exact 8-scene timings,
- captions,
- final audio mix,
- Visual QA checkpoint,
- OpenCut export,
- final MP4.

Freshness matters: a file may physically exist but be stale after upstream changes. Do not regress to existence-only readiness.

See `docs/AGENT_ONE.md`.

---

## 8. Task Engine / worker rules

Task states:

```text
queued
running
succeeded
failed
cancelled
```

Resources:

```text
cpu
gpu
io
network
```

Rules:

- only one local GPU task may run at once,
- executable stages are allow-listed,
- task payloads must not become arbitrary shell commands,
- task logs are stored under `C:\CSP\output\.studio-tasks\<task-id>.log`,
- snapshot YAMLs are written under `C:\CSP\output\.studio-snapshots`,
- current SQLite scene prompt/shot state must override stale imported YAML values,
- task success requires expected artifact validation, not merely subprocess exit code 0,
- queued GPU work should auto-wait/auto-drain rather than require manual Run after the GPU becomes free.

Current standard executable stages:

```text
regenerate_image
tts
captions
sound_design
visual_qa
opencut_export
render_final
```

Quick image regeneration is a dedicated task path:

```text
regenerate_image_quick
```

---

## 9. Quick vs Quality Regenerate

### Quality Regenerate

Current working behavior:

1. scene marked for regeneration,
2. GPU task created,
3. ComfyUI automatically checked/started on Windows,
4. Studio writes a fresh snapshot from SQLite,
5. non-target scene images are copied into a temp sandbox for reference continuity,
6. `scripts/generate_scene.py` runs the scene's configured mode,
7. generated candidate is activated only after success via Scene Operations,
8. previous asset revision is preserved,
9. downstream freshness is invalidated.

**User-confirmed:** Quality Regenerate works end-to-end. One tested quality regeneration took roughly **5 minutes**.

### Quick Regenerate

Purpose: faster draft generation.

Behavior:

- forces `image_model: z-image-turbo`,
- forces target scene `render.mode: generate`,
- preserves current Studio prompt and textual continuity context,
- intentionally bypasses FLUX edit/crop/reference rendering,
- stores result as a normal new asset revision with source `local-zimage-quick`.

**User-confirmed:** Quick Regenerate works after the ComfyUI polling timeout fix.

---

## 10. Important real bugs already found and fixed

Do not reintroduce these.

### A. Quality regeneration missing reference image

Symptom:

```text
FileNotFoundError: Scena 4: brak obrazu referencyjnego ... temp ... scene-01.png
```

Cause: regeneration ran in an isolated temp output root, but scene 4 used `reference_scene: 1`.

Fix: copy all current non-target scene images into the regeneration sandbox before generation.

**User-confirmed:** Quality regeneration works after this fix.

### B. Browser tab freeze from MutationObserver loop

Symptom: backend `/api/health` and static files returned HTTP 200 but browser page appeared non-responsive.

Cause: `actions.js` MutationObserver repeatedly rewrote `Quality Regenerate` text, generating another DOM mutation indefinitely.

Fix: mutate only when text differs and schedule injection safely.

**User-confirmed:** Studio UI became usable again after this fix.

### C. ComfyUI `/history` ReadTimeout

Symptom:

```text
requests.exceptions.ReadTimeout:
HTTPConnectionPool(host='127.0.0.1', port=8188): Read timed out. (read timeout=30)
```

Cause: one 30-second `/history/<prompt_id>` read timeout aborted generation although ComfyUI was still busy.

Fix: `generate_scene.py` uses resilient polling for transient `ReadTimeout` / connection errors until the global Comfy timeout (configured around 1200 s) is exhausted.

**User-confirmed:** Quick Regenerate works after this fix.

---

## 11. Manual Actions / production panel

PR #11 now contains Manual Actions for:

```text
TTS
Captions
Sound
Visual QA
OpenCut
Render Final
```

The panel is intended to show states:

```text
DONE
READY
BLOCKED
QUEUED
RUNNING
FAILED
```

Dependency design:

- TTS: can run independently of later production artifacts,
- Captions: requires valid TTS,
- Sound: requires valid TTS,
- Visual QA: requires active images 8/8,
- OpenCut: requires images + TTS + captions + sound,
- Render Final: requires images + TTS + captions + sound + scene review.

Visual QA is categorized as `network` because hosted NIM must not reserve the local GPU resource.

**Important:** the latest dependency/freshness/auto-drain changes were code-complete at handoff time but had **not yet been user-confirmed through the full local regression suite**. Treat them as needing verification before further architectural changes.

---

## 12. Pipeline freshness graph

`csp_studio/pipeline_state.py` centralizes invalidation.

Key intent:

### After image change

Mark stale:

```text
visual_qa
opencut_export
render_final
visual_qa_scene_XX
```

All image replacement paths should share this behavior, including:

- manual Import/Replace,
- Quick Regenerate,
- Quality Regenerate.

### After TTS regeneration

Downstream artifacts become stale:

```text
captions
sound_design
opencut_export
render_final
```

### After captions regeneration

```text
opencut_export
render_final
```

### After sound regeneration

```text
opencut_export
render_final
```

Do not treat an old `final.mp4` as current merely because it still exists after a scene/audio change.

---

## 13. Visual QA

Architecture:

- review each real active scene image individually,
- lightweight JPEG thumbnail for provider upload,
- include narration + ShotPlan + motion + continuity refs,
- aggregate verified scene reviews,
- persist report to:

```text
C:\CSP\output\001-drzwi-0\qa\visual-qa.json
```

Provider layer is configurable. A real confirmed run used:

```text
provider: nvidia_nim
model:    meta/llama-3.2-11b-vision-instruct
score:    75/100
```

The real run completed reviews for all 8 scenes plus aggregation.

Historical/generated placeholder pairs such as:

```text
specific issue -> specific fix
```

must be filtered/avoided. PR #11 includes both UI filtering and a newer source/normalization fix.

**Confirmed:** real NIM Visual QA worked.  
**Not yet reconfirmed after latest branch changes:** the newly modified Manual Actions invocation/freshness integration.

See `docs/VISUAL_QA.md`.

---

## 14. NVIDIA NIM provider layer

Provider-neutral contracts cover:

- chat,
- vision,
- embeddings.

Primary environment variables:

```text
NVIDIA_API_KEY
NVIDIA_NIM_BASE_URL
CSP_NIM_MODEL
CSP_NIM_VISION_MODEL
CSP_NIM_EMBED_MODEL
```

Do not persist secrets to SQLite/tasks/logs.

Embedding model history:

- old `nvidia/nv-embedqa-e5-v5` returned HTTP 410 / EOL,
- migrated to `nvidia/nemotron-3-embed-1b`.

Experimental media NIM uses a separate explicit base URL:

```text
NVIDIA_VISUAL_NIM_BASE_URL
```

Experimental outputs must remain candidates until explicit review/import.

See:

- `docs/NVIDIA_NIM_PROVIDER.md`
- `docs/NIM_MEDIA_EXPERIMENTS.md`

---

## 15. Universe Memory

Canonical CSP lore is stored as text/metadata in SQLite. Embeddings are disposable derived state.

Design:

- stable memory keys,
- kinds/namespaces,
- source-project provenance,
- content hashes,
- provider/model provenance,
- selective re-embedding,
- semantic search using query embeddings + cosine similarity,
- vector backend intentionally replaceable later.

**User-confirmed:**

- Universe Memory unit suite previously returned **4 OK**,
- real NIM embedding run embedded **9 items**,
- semantic ranking was sensible.

Agent Memory is advisory only and must not override deterministic Agent One readiness.

The newest Agent Memory sanitizer tests were not explicitly user-confirmed at handoff time.

See `docs/UNIVERSE_MEMORY.md`.

---

## 16. Confirmed user-visible behavior

The following have real user confirmation from the production machine:

- foundation Studio tests: 5 tests OK in an earlier milestone,
- Studio DB created with 8 scenes,
- Shot Director score reached 100/100 for the PoC state,
- manual image replacement from GUI works end-to-end,
- review-scene counter works,
- real Visual QA through NVIDIA NIM works for 8 scenes + aggregate,
- real Universe Memory embeddings/search work,
- automatic ComfyUI startup through regeneration works,
- Quality Regenerate works end-to-end,
- Quick Regenerate works end-to-end,
- Studio health endpoint responds correctly,
- browser UI works after the MutationObserver fix.

Do not discard these working paths during refactors.

---

## 17. Not yet confirmed / needs fresh verification

At handoff time, do **not** claim the following are locally verified unless Codex/user runs them:

- latest PR #11 combined regression suite after dependency/freshness/auto-drain changes,
- latest `tests.test_csp_actions`,
- latest `tests.test_csp_pipeline_state`,
- latest expanded `tests.test_csp_task_runner`,
- latest Visual QA placeholder regression tests,
- full Manual Actions sequence end-to-end,
- auto-drain behavior when two local GPU tasks overlap,
- new artifact-validation failure behavior,
- freshness migration on existing stale PoC artifacts,
- newest Agent Memory sanitizer tests,
- full stack regression from images through final render after the latest branch changes.

This distinction is important: code being present is not the same as user-confirmed integration success.

---

## 18. Recommended verification sequence for Codex

Before adding another large feature, first run the latest branch locally.

### Step 1 — update branch

```powershell
cd C:\Users\pat30\Youtube
git fetch origin
git switch feature/csp-studio-ops-dashboard
git pull origin feature/csp-studio-ops-dashboard

$py = "C:\CSP\venv\Scripts\python.exe"
$env:CSP_OUTPUT_DIR = "C:\CSP\output"
```

### Step 2 — targeted regression suite

```powershell
& $py -m unittest `
  tests.test_csp_actions `
  tests.test_csp_pipeline_state `
  tests.test_csp_task_engine `
  tests.test_csp_task_runner `
  tests.test_csp_studio_web `
  tests.test_csp_studio_scene_ops `
  tests.test_csp_agent_one `
  tests.test_csp_visual_qa `
  tests.test_csp_visual_qa_placeholders `
  tests.test_csp_agent_memory -v
```

Record the actual result. Do not infer success.

### Step 3 — start Studio

```powershell
& $py -m uvicorn csp_studio.web_app:app `
  --host 127.0.0.1 `
  --port 8765 `
  --log-level info
```

### Step 4 — real Manual Actions verification

Recommended order after a scene change:

```text
Visual QA
-> scene review/approval if needed
-> OpenCut Export
-> Render Final
```

If TTS/audio is deliberately regenerated, verify the larger chain:

```text
TTS
-> Captions
-> Sound
-> Visual QA
-> Review
-> OpenCut
-> Render Final
```

Inspect Tasks and checkpoint freshness after every stage.

### Step 5 — only then continue feature development

Good next candidates:

1. show task logs directly in the GUI,
2. make pipeline freshness/status clearer in the dashboard,
3. improve Manual Actions progress/status UX,
4. validate OpenCut materialization against a supported OpenCut API rather than private serialization,
5. continue Agent One/Universe Memory advisory improvements without weakening deterministic gates.

---

## 19. Donor/reuse decisions

Before implementing a large subsystem, read `docs/CSP_DONOR_MAP.md`.

Current high-level decisions:

- **MoneyPrinterTurbo**: adapt task/reliability patterns; do not fork the whole product,
- **OpenCut**: integrate for editing/timeline rather than recreating it,
- **opencut-classic**: conceptual timeline/model reference only,
- **youtube-automation-agent**: Agent One / publishing / analytics inspiration,
- **ffmpeg-ai**: staged CLI/diagnostics patterns,
- **multi-ai-video-factory**: conceptual reference,
- **yt-automation**: selected publishing ideas,
- **ShortGPT**: inspiration only.

CSP-owned differentiators remain:

- channel universe/master prompt,
- domain model,
- Shot Director,
- Visual Bible/continuity,
- Asset Manager,
- manual quality image workflow,
- Chatterbox/Whisper production path,
- sound design,
- deterministic Agent One,
- Universe Memory.

---

## 20. Documentation map

Use these docs for deeper detail:

```text
docs/ARCHITECTURE.md
docs/CSP_DONOR_MAP.md
docs/CSP_STUDIO_GUI_MVP.md
docs/P1_OPENCUT_TASK_ENGINE.md
docs/AGENT_ONE.md
docs/VISUAL_QA.md
docs/UNIVERSE_MEMORY.md
docs/NVIDIA_NIM_PROVIDER.md
docs/NIM_MEDIA_EXPERIMENTS.md
docs/LOCAL-FIRST-RUN.md
```

`docs/STATUS.md` describes an older CSP Automation v1 milestone and contains historical items that have since been tested or superseded. Do not use it alone as current truth.

---

## 21. Rules for the next Codex session

When starting a new Codex session, use this instruction:

```text
Read AGENTS.md and docs/CSP_STUDIO_HANDOFF.md first.
Work on branch feature/csp-studio-ops-dashboard.
Do not merge PRs without explicit approval.
Verify the current regression suite before adding large features.
Preserve working Quick/Quality Regenerate, asset history, deterministic Agent One,
OpenCut boundary, and pipeline freshness semantics.
```

Then inspect the current git diff/head before making changes because this handoff is a dated snapshot and later commits may supersede individual implementation details.
