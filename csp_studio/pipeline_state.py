from __future__ import annotations

from datetime import datetime
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


def reconcile_legacy_staleness(engine: TaskEngine, project_id: str) -> None:
    """Carry forward old image-change invalidation into downstream stages."""

    visual = engine.get_checkpoint(project_id, "visual_qa")
    if not visual or visual.get("state") != "stale":
        return

    reason = str((visual.get("metadata") or {}).get("reason") or "legacy image revision changed")
    for stage in ("opencut_export", "render_final"):
        checkpoint = engine.get_checkpoint(project_id, stage)
        if checkpoint and checkpoint.get("state") == "stale":
            continue
        if checkpoint and _checkpoint_is_newer(checkpoint, visual):
            # A downstream stage explicitly ran after the image invalidation.
            # Legacy reconciliation must not undo that newer result merely
            # because Visual QA is still stale (OpenCut does not depend on it).
            continue
        engine.set_checkpoint(
            project_id,
            stage,
            "stale",
            metadata={"reason": f"reconciled from visual_qa stale: {reason}"},
        )


def _checkpoint_is_newer(checkpoint: dict, source: dict) -> bool:
    candidate = str(checkpoint.get("updated_at") or "")
    baseline = str(source.get("updated_at") or "")
    if not candidate or not baseline:
        return False
    try:
        return datetime.fromisoformat(candidate) > datetime.fromisoformat(baseline)
    except ValueError:
        # Existing SQLite rows use sortable ISO-8601 UTC timestamps. Keep a
        # conservative fallback for hand-edited/legacy databases.
        return candidate > baseline


def is_stale(engine: TaskEngine, project_id: str, stage: str) -> bool:
    # This also performs the one-way legacy migration while the original
    # visual_qa=stale fact still exists, before a new Visual QA run can replace it.
    if stage in {"opencut_export", "render_final"}:
        reconcile_legacy_staleness(engine, project_id)
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
