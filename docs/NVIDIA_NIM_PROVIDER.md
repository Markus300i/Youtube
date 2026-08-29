# CSP Studio — NVIDIA NIM Provider

NVIDIA NIM is an **optional AI provider**, not a media/render dependency.

CSP keeps its production core unchanged:

```text
GPT Image/manual assets
Chatterbox
Whisper
OpenCut adapter
FFmpeg
SQLite Studio state
```

NIM is intended for:

```text
Agent One reasoning
Visual QA / VLM review
future Universe Memory embeddings
```

## Provider architecture

```text
CSP feature
   ↓
ChatProvider / VisionProvider / EmbeddingProvider
   ↓
provider registry
   ↓
NvidiaNimProvider
```

Files:
- `csp_studio/providers/base.py`
- `csp_studio/providers/registry.py`
- `csp_studio/providers/nvidia_nim.py`

## Configuration

Set the key only in the process environment. The key is not written into SQLite, task payloads or project files.

```powershell
$env:NVIDIA_API_KEY = "nvapi-..."
$env:CSP_AI_PROVIDER = "nvidia_nim"
```

Optional model overrides:

```powershell
$env:CSP_NIM_MODEL = "nvidia/llama-3.3-nemotron-super-49b-v1.5"
$env:CSP_NIM_VISION_MODEL = "meta/muse-glimmer-30b"
$env:CSP_NIM_EMBED_MODEL = "nvidia/nv-embedqa-e5-v5"
```

Optional endpoint override:

```powershell
$env:NVIDIA_NIM_BASE_URL = "https://integrate.api.nvidia.com/v1"
```

The endpoint override is important because the same provider can later target a self-hosted compatible NIM endpoint without changing Agent One or Visual QA code.

## Current defaults

- Chat / Agent reasoning: `nvidia/llama-3.3-nemotron-super-49b-v1.5`
- Vision / Visual QA: `meta/muse-glimmer-30b`
- Embeddings: `nvidia/nv-embedqa-e5-v5`

Model names remain configuration, not domain state. They can be changed without migrations.

## Supported operations

### Chat

Uses OpenAI-compatible:

```text
POST /v1/chat/completions
```

### Visual analysis

Local image files are validated and encoded as data URIs in `image_url` message parts. This is for small review batches such as 8 CSP scene frames, not bulk media storage.

### Embeddings

Uses:

```text
POST /v1/embeddings
```

with explicit `input_type=passage|query`.

## Testing

Tests use `httpx.MockTransport`; they do not call NVIDIA and do not require an API key.

```powershell
$py = "C:\CSP\venv\Scripts\python.exe"
& $py -m unittest tests.test_csp_nvidia_nim_provider -v
```

## Security rule

Never put `NVIDIA_API_KEY` into:
- SQLite,
- YAML,
- GitHub,
- Studio task payload/result JSON,
- log messages.

Persist only safe provenance such as:

```text
provider = nvidia_nim
model = meta/muse-glimmer-30b
operation = visual_qa
```
