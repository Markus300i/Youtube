from __future__ import annotations

import json
import os
from typing import Any
from urllib import error, request


DEFAULT_COMFY_URL = "http://127.0.0.1:8188"


def interrupt_comfyui(*, base_url: str | None = None, timeout: float = 3.0) -> dict[str, Any]:
    """Best-effort interrupt of the currently executing ComfyUI prompt.

    CSP serializes local image-generation GPU work, so when a running image
    generation task is cancelled the currently executing Comfy prompt belongs
    to that task. This helper is intentionally not called for queued tasks or
    unrelated pipeline stages.
    """

    root = (base_url or os.getenv("CSP_COMFY_URL") or DEFAULT_COMFY_URL).rstrip("/")
    req = request.Request(
        f"{root}/interrupt",
        data=b"{}",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=max(0.2, float(timeout))) as response:
            raw = response.read()
            payload = None
            if raw:
                try:
                    payload = json.loads(raw.decode("utf-8", errors="replace"))
                except json.JSONDecodeError:
                    payload = None
            return {
                "requested": True,
                "ok": 200 <= int(response.status) < 300,
                "status": int(response.status),
                "response": payload,
            }
    except (error.URLError, TimeoutError, OSError) as exc:
        return {
            "requested": True,
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
        }
