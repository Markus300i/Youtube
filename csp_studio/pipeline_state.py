from __future__ import annotations

from pathlib import Path
from typing import Iterable

from .task_engine import TaskEngine


IMAGE_DEPENDENTS = ("visual_qa", "opencut_export", "render_final")
TTS_DEPENDENTS = ("captions", "sound_design", "opencut_export", "render_final")
CAPTION_DEPENDENTS = ("opencut_export", "render_final")
SOUND_DEPENDENTS = ("opencut_export", "render_final")


def mark_stale(
    engine: TaskEngine,
    project_id: str,
    stages: Iterable[str],
    *,
    reason: str,
) -> None:
    for stage in stages:
        engine.set_checkpoint(project_id, stage, "stale", metadata={"reason": reason})


def mark_done(
    engine: TaskEngine,
    project_id: str,
    stage: str,
    *,
    artifact_path: str | Path | None = None,
    metadata: dict | None = None,
) -> None:
    engine.set_checkpoint(
        project_id,
        stage,
        "done",
        artifact_path=artifact_path,
        metadata=metadata or {},
    )


def is_stale(engine: TaskEngine, project_id: str, stage: str) -> bool:
    checkpoint = engine.get_checkpoint(project_id, stage)
    return bool(checkpoint and checkpoint.get("state") == "stale")


def invalidate_after_image_change(
    engine: TaskEngine,
    project_id: str,
    *,
    scene_id: int,
    reason: str,
) -> None:
    mark_stale(engine, project_id, IMAGE_DEPENDENTS, reason=reason)
    engine.set_checkpoint(
        project_id,
        f"visual_qa_scene_{scene_id:02d}",
        "stale",
        metadata={"reason": reason},
    )


def invalidate_after_stage(
    engine: TaskEngine,
    project_id: str,
    stage: str,
    *,
    reason: str,
) -> None:
    mapping = {
        "tts": TTS_DEPENDENTS,
        "captions": CAPTION_DEPENDENTS,
        "sound_design": SOUND_DEPENDENTS,
    }
    dependents = mapping.get(stage, ())
    if dependents:
        mark_stale(engine, project_id, dependents, reason=reason)
