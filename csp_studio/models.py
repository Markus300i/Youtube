from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(slots=True)
class ShotPlan:
    shot_type: str = "medium"
    camera: str = "static"
    purpose: str = "story"
    visual_anchor: str | None = None
    motion_intensity: str = "low"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class Scene:
    project_id: str
    scene_id: int
    text: str
    prompt: str
    continuity_refs: list[str] = field(default_factory=list)
    render: dict[str, Any] = field(default_factory=dict)
    motion: str = "static"
    shot: ShotPlan = field(default_factory=ShotPlan)
    status: str = "draft"
    asset_path: str | None = None
    revision: int = 1
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["shot"] = self.shot.to_dict()
        return data


@dataclass(slots=True)
class Project:
    project_id: str
    title: str
    series: str = ""
    fictional: bool = True
    status: str = "draft"
    narration: str = ""
    visual_style: str = ""
    source_yaml: str | None = None
    scenes: list[Scene] = field(default_factory=list)
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["scenes"] = [scene.to_dict() for scene in self.scenes]
        return data


@dataclass(slots=True)
class SceneRevision:
    project_id: str
    scene_id: int
    revision: int
    action: str
    before: dict[str, Any]
    after: dict[str, Any]
    note: str = ""
    created_at: str = field(default_factory=utc_now)
