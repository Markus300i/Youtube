from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Protocol, Sequence


@dataclass(slots=True)
class MediaResult:
    provider: str
    model: str
    kind: str
    output_path: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ImageMediaProvider(Protocol):
    name: str

    def generate_image(
        self,
        prompt: str,
        output_path: str,
        *,
        model: str | None = None,
    ) -> MediaResult: ...

    def edit_images(
        self,
        prompt: str,
        image_paths: Sequence[str],
        output_path: str,
        *,
        model: str | None = None,
    ) -> MediaResult: ...


class VideoMediaProvider(Protocol):
    name: str

    def generate_video(
        self,
        prompt: str,
        output_path: str,
        *,
        input_image: str | None = None,
        model: str | None = None,
        size: str = "832x480",
        seconds: int = 4,
    ) -> MediaResult: ...
