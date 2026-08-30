from __future__ import annotations

import base64
import mimetypes
import os
from pathlib import Path
from typing import Any, Sequence

import httpx

from .base import ProviderError, ProviderResponse

DEFAULT_BASE_URL = "https://integrate.api.nvidia.com/v1"
DEFAULT_CHAT_MODEL = "nvidia/llama-3.3-nemotron-super-49b-v1.5"
DEFAULT_VISION_MODEL = "meta/muse-glimmer-30b"
DEFAULT_EMBED_MODEL = "nvidia/nv-embedqa-e5-v5"


def _clean_api_key(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    if any(char.isspace() for char in cleaned):
        raise ProviderError(
            "NVIDIA_API_KEY contains whitespace inside the key. "
            "Set the environment variable again with the raw key only."
        )
    return cleaned


class NvidiaNimProvider:
    name = "nvidia_nim"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        chat_model: str | None = None,
        vision_model: str | None = None,
        embed_model: str | None = None,
        timeout: float = 90.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.api_key = _clean_api_key(api_key if api_key is not None else os.getenv("NVIDIA_API_KEY"))
        self.base_url = (base_url or os.getenv("NVIDIA_NIM_BASE_URL") or DEFAULT_BASE_URL).strip().rstrip("/")
        self.chat_model = (chat_model or os.getenv("CSP_NIM_MODEL") or DEFAULT_CHAT_MODEL).strip()
        self.vision_model = (vision_model or os.getenv("CSP_NIM_VISION_MODEL") or DEFAULT_VISION_MODEL).strip()
        self.embed_model = (embed_model or os.getenv("CSP_NIM_EMBED_MODEL") or DEFAULT_EMBED_MODEL).strip()
        self._client = client or httpx.Client(timeout=timeout)
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "NvidiaNimProvider":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            raise ProviderError("NVIDIA_API_KEY is not configured")
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            response = self._client.post(f"{self.base_url}/{path.lstrip('/')}", headers=self._headers(), json=payload)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:800]
            raise ProviderError(f"NVIDIA NIM HTTP {exc.response.status_code}: {detail}") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"NVIDIA NIM request failed: {exc}") from exc
        data = response.json()
        if not isinstance(data, dict):
            raise ProviderError("NVIDIA NIM returned a non-object JSON response")
        return data

    def chat(
        self,
        messages: Sequence[dict[str, Any]],
        *,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 2048,
    ) -> ProviderResponse:
        selected_model = model or self.chat_model
        payload = {
            "model": selected_model,
            "messages": list(messages),
            "temperature": max(0.0, min(1.0, float(temperature))),
            "max_tokens": int(max_tokens),
            "stream": False,
        }
        data = self._post("chat/completions", payload)
        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError("NVIDIA NIM response does not contain choices[0].message.content") from exc
        if not isinstance(text, str):
            raise ProviderError("NVIDIA NIM chat content is not text")
        return ProviderResponse(
            provider=self.name,
            model=str(data.get("model") or selected_model),
            text=text,
            raw=data,
            usage=data.get("usage") or {},
        )

    def analyze_images(
        self,
        prompt: str,
        image_paths: Sequence[str],
        *,
        model: str | None = None,
        temperature: float = 0.1,
        max_tokens: int = 1600,
    ) -> ProviderResponse:
        if not image_paths:
            raise ValueError("At least one image is required")
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for raw_path in image_paths:
            path = Path(raw_path).expanduser().resolve()
            if not path.is_file():
                raise FileNotFoundError(path)
            mime = mimetypes.guess_type(path.name)[0] or "image/png"
            encoded = base64.b64encode(path.read_bytes()).decode("ascii")
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{encoded}"},
                }
            )
        return self.chat(
            [{"role": "user", "content": content}],
            model=model or self.vision_model,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    def embed(
        self,
        texts: Sequence[str],
        *,
        model: str | None = None,
        input_type: str = "passage",
    ) -> list[list[float]]:
        if input_type not in {"passage", "query"}:
            raise ValueError("input_type must be 'passage' or 'query'")
        items = [str(text) for text in texts]
        if not items:
            return []
        selected_model = model or self.embed_model
        data = self._post(
            "embeddings",
            {
                "model": selected_model,
                "input": items,
                "input_type": input_type,
                "encoding_format": "float",
                "truncate": "END",
            },
        )
        rows = data.get("data")
        if not isinstance(rows, list):
            raise ProviderError("NVIDIA NIM embedding response does not contain data[]")
        ordered = sorted(rows, key=lambda row: int(row.get("index", 0)))
        embeddings: list[list[float]] = []
        for row in ordered:
            vector = row.get("embedding")
            if not isinstance(vector, list):
                raise ProviderError("NVIDIA NIM embedding row has no embedding vector")
            embeddings.append([float(value) for value in vector])
        return embeddings
