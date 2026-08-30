from __future__ import annotations

import base64
import mimetypes
import os
from pathlib import Path
from typing import Any, Sequence

import httpx

from .base import ProviderError, ProviderResponse

DEFAULT_BASE_URL = "https://integrate.api.nvidia.com/v1"
DEFAULT_CHAT_MODEL = "nvidia/nemotron-3.5-lightning-30b-a3b"
DEFAULT_VISION_MODEL = "meta/llama-3.2-11b-vision-instruct"
DEFAULT_EMBED_MODEL = "nvidia/nemotron-3-embed-1b"
DEFAULT_TIMEOUT = 90.0
DEFAULT_VISION_TIMEOUT = 120.0
DEFAULT_VISION_RETRIES = 0
RETIRED_CHAT_MODELS = {
    "nvidia/llama-3.3-nemotron-super-49b-v1.5": DEFAULT_CHAT_MODEL,
}
RETIRED_EMBED_MODELS = {
    "nvidia/nv-embedqa-e5-v5": DEFAULT_EMBED_MODEL,
}


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


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw.strip())
    except ValueError as exc:
        raise ProviderError(f"{name} must be a number") from exc
    if value <= 0:
        raise ProviderError(f"{name} must be greater than zero")
    return value


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except ValueError as exc:
        raise ProviderError(f"{name} must be an integer") from exc
    if value < 0:
        raise ProviderError(f"{name} cannot be negative")
    return value


def _migrate_retired_chat_model(value: str) -> str:
    cleaned = value.strip()
    return RETIRED_CHAT_MODELS.get(cleaned, cleaned)


def _migrate_retired_embed_model(value: str) -> str:
    cleaned = value.strip()
    return RETIRED_EMBED_MODELS.get(cleaned, cleaned)


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
        timeout: float | None = None,
        vision_timeout: float | None = None,
        vision_retries: int | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.api_key = _clean_api_key(api_key if api_key is not None else os.getenv("NVIDIA_API_KEY"))
        self.base_url = (base_url or os.getenv("NVIDIA_NIM_BASE_URL") or DEFAULT_BASE_URL).strip().rstrip("/")
        self.chat_model = _migrate_retired_chat_model(chat_model or os.getenv("CSP_NIM_MODEL") or DEFAULT_CHAT_MODEL)
        self.vision_model = (vision_model or os.getenv("CSP_NIM_VISION_MODEL") or DEFAULT_VISION_MODEL).strip()
        self.embed_model = _migrate_retired_embed_model(
            embed_model or os.getenv("CSP_NIM_EMBED_MODEL") or DEFAULT_EMBED_MODEL
        )
        self.timeout = float(timeout if timeout is not None else _env_float("CSP_NIM_TIMEOUT", DEFAULT_TIMEOUT))
        self.vision_timeout = float(
            vision_timeout if vision_timeout is not None else _env_float("CSP_NIM_VISION_TIMEOUT", DEFAULT_VISION_TIMEOUT)
        )
        self.vision_retries = int(
            vision_retries if vision_retries is not None else _env_int("CSP_NIM_VISION_RETRIES", DEFAULT_VISION_RETRIES)
        )
        if self.timeout <= 0 or self.vision_timeout <= 0:
            raise ProviderError("NIM timeouts must be greater than zero")
        if self.vision_retries < 0:
            raise ProviderError("vision_retries cannot be negative")
        self._client = client or httpx.Client(timeout=self.timeout)
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

    def _post(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        timeout: float | None = None,
        retries: int = 0,
    ) -> dict[str, Any]:
        url = f"{self.base_url}/{path.lstrip('/')}"
        request_timeout = self.timeout if timeout is None else float(timeout)
        attempt = 0
        while True:
            try:
                response = self._client.post(
                    url,
                    headers=self._headers(),
                    json=payload,
                    timeout=request_timeout,
                )
                response.raise_for_status()
            except httpx.ReadTimeout as exc:
                if attempt < retries:
                    attempt += 1
                    continue
                raise ProviderError(
                    f"NVIDIA NIM timed out after {request_timeout:.0f}s "
                    f"({attempt + 1} attempt(s)): {path}"
                ) from exc
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
        request_timeout: float | None = None,
        retries: int = 0,
    ) -> ProviderResponse:
        selected_model = _migrate_retired_chat_model(model or self.chat_model)
        payload = {
            "model": selected_model,
            "messages": list(messages),
            "temperature": max(0.0, min(1.0, float(temperature))),
            "max_tokens": int(max_tokens),
            "stream": False,
        }
        data = self._post(
            "chat/completions",
            payload,
            timeout=request_timeout,
            retries=retries,
        )
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
            request_timeout=self.vision_timeout,
            retries=self.vision_retries,
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
        selected_model = _migrate_retired_embed_model(model or self.embed_model)
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
