# AGENTS.md — CSP Studio / Ciemna Strona Polski

This repository contains **CSP Studio**, the local-first production and automation system for the fictional YouTube Shorts channel **Ciemna Strona Polski**.

Before making non-trivial changes, read:

1. `docs/CSP_STUDIO_HANDOFF.md` — current project state and verified/unverified behavior.
2. `docs/ARCHITECTURE.md` — base production architecture.
3. `docs/CSP_DONOR_MAP.md` — reuse/adapt decisions for external projects.
4. Module docs relevant to the task, especially `docs/AGENT_ONE.md`, `docs/P1_OPENCUT_TASK_ENGINE.md`, `docs/VISUAL_QA.md`, `docs/UNIVERSE_MEMORY.md`, `docs/NVIDIA_NIM_PROVIDER.md`, and `docs/NIM_MEDIA_EXPERIMENTS.md`.

## Non-negotiable project rules

- Do **not** merge pull requests unless the user explicitly asks for a merge.
- Do **not** rewrite CSP Studio from scratch when the existing layered architecture can be extended.
- Do **not** claim tests passed unless they were actually executed and the result is available.
- Do **not** persist or print API keys, tokens, passwords, voice secrets, or other credentials.
- Keep channel stories explicitly fictional. Never change the system so fictional CSP stories are presented as real events.
- Preserve the existing renderer compatibility unless a migration is deliberate, tested, and documented.
- Prefer small reversible changes with tests over broad refactors.

## Architecture boundary

CSP Studio owns **WHAT**:

- story/script and project domain,
- scene plans and prompts,
- Shot Director / Visual Bible / continuity intent,
- assets, revisions, approvals and history,
- Task Engine and pipeline checkpoints,
- Agent One readiness/orchestration,
- Visual QA,
- Universe Memory,
- provider integrations,
- production status and review.

OpenCut owns **HOW the edit is assembled**:

- timeline and tracks,
- clip ordering and duration,
- trim/keyframes/motion,
- transitions,
- audio/text tracks,
- playback and editor interaction,
- eventual headless/editor export when its supported API is suitable.

Do not rebuild a full NLE/timeline/keyframe/multitrack editor inside CSP Studio. The current boundary is the versioned `csp-opencut-interchange/1` contract.

## Current working branch

The active stacked development branch is:

```text
feature/csp-studio-ops-dashboard
```

It is PR #11 and is stacked on PR #10 rather than directly on `main`. Do not casually retarget, squash, merge, or flatten the PR stack.

## Local production environment

Primary Windows environment:

```text
Repo:        C:\Users\pat30\Youtube
Python:      C:\CSP\venv\Scripts\python.exe
Output:      C:\CSP\output
SQLite:      C:\CSP\output\csp-studio.db
Studio URL:  http://127.0.0.1:8765/
Project ID:  001
Project:     Drzwi 0
Project dir: C:\CSP\output\001-drzwi-0
Hardware:    RTX 4060 Ti 8 GB, 32 GB RAM
```

Use PowerShell examples for local instructions.

Typical setup:

```powershell
cd C:\Users\pat30\Youtube
$py = "C:\CSP\venv\Scripts\python.exe"
$env:CSP_OUTPUT_DIR = "C:\CSP\output"
```

Studio server:

```powershell
& $py -m uvicorn csp_studio.web_app:app --host 127.0.0.1 --port 8765 --log-level info
```

Health check:

```powershell
Invoke-RestMethod http://127.0.0.1:8765/api/health
```

## Resource constraints

The target GPU has 8 GB VRAM. Preserve these assumptions:

- one heavy local GPU task at a time,
- ComfyUI generation is serialized,
- models should be unloaded/freed where appropriate,
- avoid concurrent local diffusion + Chatterbox workloads unless proven safe,
- Visual QA using hosted NVIDIA NIM is a network task, not a local GPU task.

## Image generation modes

- **Quick Regenerate**: Z-Image Turbo draft path; intentionally skips FLUX edit/crop for speed.
- **Quality Regenerate**: preserves the scene's configured `render.mode`, including reference/crop/FLUX-edit behavior.
- Manual GPT Image/browser assets remain first-class reviewed assets.
- Experimental NVIDIA media NIM outputs remain candidates only; never auto-promote them to canonical scene assets.

Any scene-image replacement must preserve revision history and invalidate downstream freshness (Visual QA/OpenCut/final render).

## Task and readiness rules

- `AgentOne.inspect()` owns deterministic readiness facts.
- Provider/LLM output may explain state but must never override failed deterministic gates.
- Task payloads must not enable arbitrary shell execution; executable stages are allow-listed.
- GPU claims are serialized.
- A stage must not be marked successful merely because a subprocess returned code 0; expected artifacts must exist and be non-empty.
- Freshness/checkpoint state matters: an old file on disk is not automatically a current artifact after upstream changes.

## Testing expectations

Run targeted tests for every change. For broader changes, include the relevant regression set. Useful suites include:

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

Use mocked provider tests for ordinary CI/unit coverage. Real NVIDIA NIM calls and real ComfyUI runs are explicit integration tests and may require local environment variables/services.

## Working style for Codex

For each task:

1. Inspect the current implementation and relevant docs first.
2. Check `docs/CSP_DONOR_MAP.md` before implementing a large subsystem that may already have a donor/reference project.
3. Preserve compatibility with the stacked branch unless the user explicitly requests a migration.
4. Implement the smallest coherent change.
5. Add/update tests.
6. Run the tests that can actually be run in the current environment.
7. Clearly separate code-complete status from user-confirmed local integration status.
8. Report exact files changed, tests executed, failures/limitations, and the next useful verification step.

The source of truth for the current handoff is `docs/CSP_STUDIO_HANDOFF.md`.