# CSP Studio — Visual QA

Visual QA compares the **actual eight active scene frames**, not only Shot Director metadata.

## Goal

Catch the quality problem visible in the first `Drzwi 0` proof of concept:
- repeated frontal compositions,
- insufficient visual resets,
- continuity mistakes,
- AI-looking humans/hands/faces,
- weak mobile readability,
- frame/narration mismatch.

## Flow

```text
8 active image revisions
        ↓
local QA thumbnails (JPEG ≤432×768)
        ↓
VisionProvider
        ↓
NVIDIA NIM VLM by default
        ↓
structured JSON report
        ↓
qa/visual-qa.json
        ↓
TaskEngine checkpoint: visual_qa=done
        ↓
Agent One sees QA completed
```

Full production PNGs are not modified. Thumbnails are generated under:

```text
<project>/qa/thumbnails/
```

## Default NVIDIA model

The provider layer currently defaults Visual QA to:

```text
meta/muse-glimmer-30b
```

The model remains configurable through:

```powershell
$env:CSP_NIM_VISION_MODEL = "meta/muse-glimmer-30b"
```

## Report shape

```json
{
  "project_id": "001",
  "score": 74,
  "provider": "nvidia_nim",
  "model": "meta/muse-glimmer-30b",
  "summary": "...",
  "warnings": [],
  "continuity": [],
  "monotony": [],
  "scene_notes": [
    {
      "scene_id": 4,
      "severity": "warning",
      "issue": "...",
      "recommendation": "..."
    }
  ],
  "shot_director_score": 100,
  "shot_director_warnings": []
}
```

Shot Director structural QA is included next to VLM visual QA. The two signals are complementary:

```text
Shot Director → planned visual language
VLM QA        → what is actually visible in pixels
```

## Failure behavior

Visual QA only creates a `done` checkpoint after:
1. all eight active images exist,
2. the provider returns parseable JSON,
3. the report is atomically written.

If any step fails, checkpoint state becomes `failed` with safe error metadata.

## Run

```powershell
cd C:\CSP\Youtube
$py = "C:\CSP\venv\Scripts\python.exe"
$env:CSP_OUTPUT_DIR = "C:\CSP\output"
$env:NVIDIA_API_KEY = "nvapi-..."

& $py -m csp_studio.visual_qa 001
```

The first real NIM run is intentionally manual/reviewed. NIM is optional; other `VisionProvider` implementations can replace it later.

## Tests

Tests use a fake VLM and make no external API call:

```powershell
& $py -m unittest tests.test_csp_visual_qa -v
```
