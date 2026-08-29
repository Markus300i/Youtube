from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Protocol, Sequence


class ProviderError(RuntimeError):
    pass


@dataclass(slots=True)
class ProviderResponse:
    provider: str
    model: str
    text: str
    raw: dict[str, Any] = field(default_factory=dict)
    usage: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ChatProvider(Protocol):
    name: str

    def chat(
        self,
        messages: Sequence[dict[str, Any]],
        *,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 2048,
    ) -> ProviderResponse: ...


class VisionProvider(Protocol):
    name: str

    def analyze_images(
        self,
        prompt: str,
        image_paths: Sequence[str],
        *,
        model: str | None = None,
        temperature: float = 0.1,
        max_tokens: int = 1600,
    ) -> ProviderResponse: ...


class EmbeddingProvider(Protocol):
    name: str

    def embed(
        self,
        texts: Sequence[str],
        *,
        model: str | None = None,
        input_type: str = "passage",
    ) -> list[list[float]]: ...
