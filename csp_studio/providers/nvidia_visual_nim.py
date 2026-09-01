from __future__ import annotations

import base64
import mimetypes
import os
from pathlib import Path
from typing import Any, Sequence

import httpx

from ..local_env import get_local_setting
from .base import ProviderError
from .media import MediaResult

DEFAULT_IMAGE_MODEL = "black-forest-labs/flux.2-klein-4b"
DEFAULT_VIDEO_MODEL = "wan-ai/wan2.2"


class NvidiaVisualNimProvider:
    """Experimental Visual GenAI NIM adapter.

    No endpoint is assumed. Configure NVIDIA_VISUAL_NIM_BASE_URL explicitly for a
    self-hosted or partner-hosted OpenAI-compatible Visual GenAI NIM. This keeps the
    stable CSP production path independent from changing catalog endpoints.
    """

    name = "nvidia_visual_nim"

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        image_model: str | None = None,
        video_model: str | None = None,
        timeout: float = 300.0,
        client: httpx.Client | None = None,
    ) -> None:
        configured = base_url or get_local_setting("NVIDIA_VISUAL_NIM_BASE_URL")
        if not configured:
            raise ProviderError(
                "NVIDIA_VISUAL_NIM_BASE_URL is not configured; media experiments are disabled by default"
            )
        self.base_url = configured.rstrip("/")
        self.api_key = api_key if api_key is not None else get_local_setting("NVIDIA_API_KEY")
        self.image_model = image_model or get_local_setting("CSP_NIM_IMAGE_MODEL") or DEFAULT_IMAGE_MODEL
        self.video_model = video_model or get_local_setting("CSP_NIM_VIDEO_MODEL") or DEFAULT_VIDEO_MODEL
        self._client = client or httpx.Client(timeout=timeout)
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "NvidiaVisualNimProvider":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            response = self._client.post(
                f"{self.base_url}/{path.lstrip('/')}",
                headers=self._headers(),
                json=payload,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:1000]
            raise ProviderError(f"NVIDIA Visual NIM HTTP {exc.response.status_code}: {detail}") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"NVIDIA Visual NIM request failed: {exc}") from exc
        data = response.json()
        if not isinstance(data, dict):
            raise ProviderError("NVIDIA Visual NIM returned a non-object JSON response")
        return data

    @staticmethod
    def _write_b64(path: str | Path, encoded: str) -> Path:
        target = Path(path).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            payload = base64.b64decode(encoded, validate=True)
        except Exception as exc:
            raise ProviderError("NVIDIA Visual NIM returned invalid base64 media") from exc
        if not payload:
            raise ProviderError("NVIDIA Visual NIM returned empty media")
        temp = target.with_suffix(target.suffix + ".tmp")
        try:
            temp.write_bytes(payload)
            os.replace(temp, target)
        finally:
            temp.unlink(missing_ok=True)
        return target

    @staticmethod
    def _data_uri(path: str | Path) -> str:
        source = Path(path).expanduser().resolve()
        if not source.is_file():
            raise FileNotFoundError(source)
        mime = mimetypes.guess_type(source.name)[0] or "image/png"
        encoded = base64.b64encode(source.read_bytes()).decode("ascii")
        return f"data:{mime};base64,{encoded}"

    def generate_image(
        self,
        prompt: str,
        output_path: str,
        *,
        model: str | None = None,
        seed: int | None = None,
        steps: int | None = None,
    ) -> MediaResult:
        selected_model = model or self.image_model
        payload: dict[str, Any] = {
            "model": selected_model,
            "prompt": prompt,
            "n": 1,
            "response_format": "b64_json",
        }
        if seed is not None:
            payload["seed"] = int(seed)
        if steps is not None:
            payload["steps"] = int(steps)
        data = self._post("images/generations", payload)
        try:
            encoded = data["data"][0]["b64_json"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError("Image generation response has no data[0].b64_json") from exc
        target = self._write_b64(output_path, str(encoded))
        return MediaResult(
            provider=self.name,
            model=selected_model,
            kind="image_generation",
            output_path=str(target),
            metadata={"seed": seed, "steps": steps},
        )

    def edit_images(
        self,
        prompt: str,
        image_paths: Sequence[str],
        output_path: str,
        *,
        model: str | None = None,
        seed: int | None = None,
        steps: int | None = None,
    ) -> MediaResult:
        if not image_paths:
            raise ValueError("At least one input image is required")
        if len(image_paths) > 8:
            raise ValueError("FLUX.2-klein OpenAI-compatible edits support at most 8 input images")
        selected_model = model or self.image_model
        payload: dict[str, Any] = {
            "model": selected_model,
            "prompt": prompt,
            "image": [self._data_uri(path) for path in image_paths],
            "n": 1,
            "response_format": "b64_json",
        }
        if seed is not None:
            payload["seed"] = int(seed)
        if steps is not None:
            payload["steps"] = int(steps)
        data = self._post("images/edits", payload)
        try:
            encoded = data["data"][0]["b64_json"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError("Image edit response has no data[0].b64_json") from exc
        target = self._write_b64(output_path, str(encoded))
        return MediaResult(
            provider=self.name,
            model=selected_model,
            kind="image_edit",
            output_path=str(target),
            metadata={"input_images": len(image_paths), "seed": seed, "steps": steps},
        )

    def generate_video(
        self,
        prompt: str,
        output_path: str,
        *,
        input_image: str | None = None,
        model: str | None = None,
        size: str = "832x480",
        seconds: int = 4,
    ) -> MediaResult:
        selected_model = model or self.video_model
        payload: dict[str, Any] = {
            "model": selected_model,
            "prompt": prompt,
            "size": size,
            "seconds": int(seconds),
        }
        mode = "text_to_video"
        if input_image:
            payload["input_reference"] = self._data_uri(input_image)
            mode = "image_to_video"
        data = self._post("videos/generations", payload)
        try:
            encoded = data["data"]["b64_json"]
        except (KeyError, TypeError) as exc:
            raise ProviderError("Video generation response has no data.b64_json") from exc
        target = self._write_b64(output_path, str(encoded))
        return MediaResult(
            provider=self.name,
            model=selected_model,
            kind=mode,
            output_path=str(target),
            metadata={"size": size, "seconds": int(seconds)},
        )
