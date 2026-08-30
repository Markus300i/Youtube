# CSP Studio — NVIDIA Visual NIM Media Experiments

This module is **experimental and disabled by default**.

It must not replace the current production path automatically:

```text
GPT Image/browser → reviewed CSP scene asset → OpenCut/CSP render
```

## Why separate it

NVIDIA exposes OpenAI-compatible Visual GenAI endpoints for image generation/editing and Wan2.2 video generation. These are useful for A/B experiments, but endpoint availability, deployment requirements and model quality can change independently from CSP production.

Therefore the experiment requires an explicit endpoint:

```powershell
$env:NVIDIA_VISUAL_NIM_BASE_URL = "http://your-visual-nim-host:8000/v1"
```

If the endpoint needs authentication:

```powershell
$env:NVIDIA_API_KEY = "nvapi-..."
```

No visual endpoint is assumed and nothing is enabled by setting the normal LLM NIM key alone.

## Image candidate

Default:

```text
black-forest-labs/flux.2-klein-4b
```

Adapter methods:

```text
generate_image()
edit_images()
```

OpenAI-compatible endpoints:

```text
POST /v1/images/generations
POST /v1/images/edits
```

FLUX.2-klein supports up to 8 input images for the OpenAI-compatible edit endpoint. This makes it suitable for controlled experiments using scene/continuity references.

## Video candidate

Default:

```text
wan-ai/wan2.2
```

Endpoint:

```text
POST /v1/videos/generations
```

The same canonical model name is used for T2V and I2V; the deployed NIM server variant decides which mode is available.

Text-to-video payload uses prompt/size/seconds. Image-to-video adds:

```text
input_reference = data:image/...;base64,...
```

CSP's first useful experiment should be image-to-video from an already approved scene image with subtle motion, not text-to-video replacement of the entire scene pipeline.

## Important local hardware boundary

The current Wan2.2 NIM is not a local RTX 4060 Ti target. NVIDIA's published minimum for the NIM deployment is 80 GB GPU memory, with larger multi-GPU configurations recommended.

Therefore:
- CSP Studio keeps OpenCut/FFmpeg for normal montage,
- Wan2.2 is external-endpoint-only for our current hardware,
- generated clips are candidates until manually reviewed,
- no generated clip overwrites canonical production media automatically.

## Provider

Files:
- `csp_studio/providers/media.py`
- `csp_studio/providers/nvidia_visual_nim.py`

`NvidiaVisualNimProvider` supports:
- FLUX image generation,
- FLUX multi-reference image editing,
- Wan2.2 T2V,
- Wan2.2 I2V.

Returned bytes are written to an explicit caller-selected path. Integration with Asset Manager is intentionally not automatic in this experiment.

## Tests

All tests use mocked HTTP responses and do not require NVIDIA infrastructure:

```powershell
$py = "C:\CSP\venv\Scripts\python.exe"
& $py -m unittest tests.test_csp_nvidia_visual_nim -v
```

## Production promotion criteria

A media provider may become a normal CSP option only after:
1. stable endpoint/API contract,
2. predictable cost/quota,
3. repeatable output quality on CSP scenes,
4. continuity test across at least 8-scene Short,
5. no regression against GPT Image/manual workflow,
6. explicit Asset Manager import/review instead of silent replacement.
