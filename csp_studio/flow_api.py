from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .log_safety import safe_exception_message
from .new_short_wizard import NewShortWizard, WizardValidationError
from .production_run import ProductionRunCoordinator
from .providers import get_provider
from .providers.nvidia_nim import nvidia_nim_status
from .store import StudioStore
from .visual_bible import VALID_KINDS, VisualBible, VisualBibleEntity
from .wizard_v2 import WizardV2, WizardV2Error, create_reviewed_wizard_v2
from .worker_registry import WorkerRegistry

ROOT = Path(__file__).resolve().parents[1]
WEB_DIR = Path(__file__).resolve().parent / "web"
OUTPUT_ROOT = Path(os.getenv("CSP_OUTPUT_DIR", str(ROOT / "output"))).expanduser().resolve()
DB_PATH = Path(os.getenv("CSP_STUDIO_DB", str(OUTPUT_ROOT / "csp-studio.db"))).expanduser().resolve()
SHORTS_DIR = ROOT / "shorts"

router = APIRouter()


class VisualBibleEntityInput(BaseModel):
    entity_key: str = Field(min_length=1, max_length=80)
    kind: str
    name: str = Field(min_length=1, max_length=160)
    description: str = ""
    prompt_fragment: str = ""
    reference_asset_path: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    active: bool = True


class VisualBibleAssignmentInput(BaseModel):
    entity_keys: list[str] = Field(default_factory=list)


class WizardV2DraftInput(BaseModel):
    topic: str = Field(min_length=3, max_length=2000)
    project_id: str | None = Field(default=None, max_length=64)
    title: str | None = Field(default=None, max_length=200)
    provider: str | None = Field(default=None, max_length=80)


def _production_payload(store: StudioStore, project_id: str) -> dict[str, Any]:
    coordinator = ProductionRunCoordinator(store, output_root=OUTPUT_ROOT)
    run = coordinator.status(project_id)
    report = coordinator.agent.inspect(project_id)
    return {"run": run.to_dict(), "agent": report.to_dict()}


@router.get("/flow.js")
def flow_js():
    return FileResponse(WEB_DIR / "flow.js", media_type="application/javascript")


@router.get("/flow-v2.js")
def flow_v2_js():
    return FileResponse(WEB_DIR / "flow_v2.js", media_type="application/javascript")


@router.get("/api/providers/nvidia-nim/status")
def nvidia_nim_configuration_status():
    try:
        return nvidia_nim_status()
    except Exception as exc:
        raise HTTPException(500, safe_exception_message(exc)) from exc


@router.get("/api/workers")
def worker_status():
    with StudioStore(DB_PATH) as store:
        workers = WorkerRegistry(store).list(online_ttl_seconds=20)
        online = [item for item in workers if item.online]
        return {
            "online": len(online),
            "total": len(workers),
            "workers": [item.to_dict() for item in workers],
        }


@router.get("/api/projects/{project_id}/production-run")
def production_run_status(project_id: str):
    with StudioStore(DB_PATH) as store:
        try:
            return _production_payload(store, project_id)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc


@router.post("/api/projects/{project_id}/production-run/start")
def production_run_start(project_id: str):
    with StudioStore(DB_PATH) as store:
        try:
            return ProductionRunCoordinator(store, output_root=OUTPUT_ROOT).start(project_id)
        except (KeyError, RuntimeError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc


@router.post("/api/projects/{project_id}/production-run/advance")
def production_run_advance(project_id: str):
    with StudioStore(DB_PATH) as store:
        try:
            return ProductionRunCoordinator(store, output_root=OUTPUT_ROOT).advance(project_id)
        except (KeyError, RuntimeError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc


@router.post("/api/projects/{project_id}/production-run/stop")
def production_run_stop(project_id: str):
    with StudioStore(DB_PATH) as store:
        try:
            run = ProductionRunCoordinator(store, output_root=OUTPUT_ROOT).stop(project_id)
            return {"run": run.to_dict()}
        except (KeyError, RuntimeError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc


@router.post("/api/wizard/projects")
def create_project_from_wizard(payload: dict[str, Any]):
    with StudioStore(DB_PATH) as store:
        try:
            return NewShortWizard(store, shorts_dir=SHORTS_DIR).create(payload)
        except FileExistsError as exc:
            raise HTTPException(409, f"Source YAML already exists: {exc}") from exc
        except (WizardValidationError, KeyError, TypeError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc


@router.post("/api/wizard/v2/draft")
def draft_project_v2(payload: WizardV2DraftInput):
    provider = None
    try:
        provider = get_provider(payload.provider)
        return WizardV2(provider).draft(
            payload.topic,
            project_id=payload.project_id,
            title=payload.title,
        ).to_dict()
    except WizardV2Error as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, safe_exception_message(exc)) from exc
    finally:
        close = getattr(provider, "close", None)
        if callable(close):
            close()


@router.post("/api/wizard/v2/create")
def create_project_v2(payload: dict[str, Any]):
    with StudioStore(DB_PATH) as store:
        try:
            return create_reviewed_wizard_v2(store, shorts_dir=SHORTS_DIR, envelope=payload)
        except FileExistsError as exc:
            raise HTTPException(409, f"Source YAML already exists: {exc}") from exc
        except (WizardV2Error, WizardValidationError, KeyError, TypeError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc


@router.get("/api/projects/{project_id}/visual-bible")
def visual_bible(project_id: str):
    with StudioStore(DB_PATH) as store:
        if store.conn.execute("SELECT 1 FROM projects WHERE project_id=?", (project_id,)).fetchone() is None:
            raise HTTPException(404, f"Unknown project: {project_id}")
        bible = VisualBible(store)
        entities = [item.to_dict() for item in bible.list(project_id, active_only=False)]
        assignments = {
            str(scene.scene_id): [item.entity_key for item in bible.assigned(project_id, scene.scene_id)]
            for scene in store.list_scenes(project_id)
        }
        return {"project_id": project_id, "valid_kinds": sorted(VALID_KINDS), "entities": entities, "assignments": assignments}


@router.post("/api/projects/{project_id}/visual-bible/entities")
def upsert_visual_bible_entity(project_id: str, payload: VisualBibleEntityInput):
    if payload.kind not in VALID_KINDS:
        raise HTTPException(400, f"Unsupported Visual Bible kind: {payload.kind}")
    with StudioStore(DB_PATH) as store:
        bible = VisualBible(store)
        try:
            entity = bible.upsert(
                VisualBibleEntity(
                    project_id=project_id,
                    entity_key=payload.entity_key.strip(),
                    kind=payload.kind,
                    name=payload.name.strip(),
                    description=payload.description.strip(),
                    prompt_fragment=payload.prompt_fragment.strip(),
                    reference_asset_path=payload.reference_asset_path,
                    metadata=payload.metadata,
                    active=payload.active,
                )
            )
        except (KeyError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc
        return entity.to_dict()


@router.put("/api/projects/{project_id}/scenes/{scene_id}/visual-bible")
def assign_visual_bible(project_id: str, scene_id: int, payload: VisualBibleAssignmentInput):
    with StudioStore(DB_PATH) as store:
        bible = VisualBible(store)
        try:
            assigned = bible.assign(project_id, scene_id, payload.entity_keys)
            scene = store.get_scene(project_id, scene_id)
            assert scene is not None
            return {
                "project_id": project_id,
                "scene_id": scene_id,
                "entity_keys": assigned,
                "prompt_context": bible.prompt_context(project_id, scene_id),
                "compiled_prompt": bible.compile_prompt(scene),
                "canonical_prompt": scene.prompt,
            }
        except (KeyError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc


@router.get("/api/projects/{project_id}/scenes/{scene_id}/visual-bible")
def scene_visual_bible(project_id: str, scene_id: int):
    with StudioStore(DB_PATH) as store:
        scene = store.get_scene(project_id, scene_id)
        if scene is None:
            raise HTTPException(404, f"Unknown scene: {project_id}:{scene_id}")
        bible = VisualBible(store)
        return {
            "project_id": project_id,
            "scene_id": scene_id,
            "entities": [item.to_dict() for item in bible.assigned(project_id, scene_id)],
            "prompt_context": bible.prompt_context(project_id, scene_id),
            "compiled_prompt": bible.compile_prompt(scene),
            "canonical_prompt": scene.prompt,
        }
