from __future__ import annotations

import json
import os
import re
import unicodedata
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException

from .agent_one import AgentOne
from .store import StudioStore
from .task_engine import TaskEngine
from .task_runner import run_task
from .universe_memory import UniverseMemory

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_ROOT = Path(os.getenv("CSP_OUTPUT_DIR", str(ROOT / "output"))).expanduser().resolve()
DB_PATH = Path(os.getenv("CSP_STUDIO_DB", str(OUTPUT_ROOT / "csp-studio.db"))).expanduser().resolve()

router = APIRouter()

PLACEHOLDER_NOTES = {
    ("specific issue", "specific fix"),
    ("issue", "recommendation"),
}


def _slug(value: str) -> str:
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-zA-Z0-9]+", "-", ascii_value).strip("-").lower() or "project"


def _project_row(store: StudioStore, project_id: str):
    row = store.conn.execute("SELECT * FROM projects WHERE project_id=?", (project_id,)).fetchone()
    if row is None:
        raise HTTPException(404, f"Unknown project: {project_id}")
    return row


def _project_dir(store: StudioStore, project_id: str) -> Path:
    row = _project_row(store, project_id)
    return OUTPUT_ROOT / f"{project_id}-{_slug(row['title'])}"


def _clean_scene_notes(raw_notes: Any) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for raw in raw_notes if isinstance(raw_notes, list) else []:
        if not isinstance(raw, dict):
            continue
        issue = str(raw.get("issue") or "").strip()
        recommendation = str(raw.get("recommendation") or "").strip()
        if not issue and not recommendation:
            continue
        if (issue.lower(), recommendation.lower()) in PLACEHOLDER_NOTES:
            continue
        output.append(dict(raw))
    return output


def _read_visual_qa(store: StudioStore, project_id: str) -> dict[str, Any]:
    path = _project_dir(store, project_id) / "qa" / "visual-qa.json"
    if not path.is_file():
        return {"available": False, "path": str(path)}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return {"available": False, "path": str(path), "error": f"{type(exc).__name__}: {exc}"}
    if not isinstance(data, dict):
        return {"available": False, "path": str(path), "error": "Visual QA report is not an object"}
    return {
        "available": True,
        "path": str(path),
        "score": int(data.get("score", 0)),
        "summary": str(data.get("summary") or ""),
        "warnings": list(data.get("warnings") or []),
        "continuity": list(data.get("continuity") or []),
        "monotony": list(data.get("monotony") or []),
        "scene_notes": _clean_scene_notes(data.get("scene_notes")),
        "aggregate_status": str(data.get("aggregate_status") or "unknown"),
        "provider": str(data.get("provider") or "unknown"),
        "model": str(data.get("model") or "unknown"),
    }


def _memory_status(store: StudioStore, project_id: str) -> dict[str, Any]:
    memory = UniverseMemory(store)
    items = memory.list()
    current = [item for item in items if item.source_project_id == project_id]
    previous = [item for item in items if item.source_project_id and item.source_project_id != project_id]
    previous_projects = sorted({item.source_project_id for item in previous if item.source_project_id})
    return {
        "total_items": len(items),
        "current_project_items": len(current),
        "previous_project_items": len(previous),
        "previous_project_ids": previous_projects,
        "comparison_available": bool(previous_projects),
    }


def _schedule(background_tasks: BackgroundTasks, task_id: str) -> None:
    background_tasks.add_task(run_task, task_id, db_path=DB_PATH, output_root=OUTPUT_ROOT)


@router.get("/api/projects/{project_id}/ops-dashboard")
def ops_dashboard(project_id: str):
    with StudioStore(DB_PATH) as store:
        _project_row(store, project_id)
        agent = AgentOne(store, output_root=OUTPUT_ROOT)
        report = agent.inspect(project_id)
        scenes = store.list_scenes(project_id)
        approved_ids = [scene.scene_id for scene in scenes if scene.status in {"approved", "render_ready"}]
        pending_ids = [scene.scene_id for scene in scenes if scene.status not in {"approved", "render_ready"}]
        tasks = [task.to_dict() for task in agent.tasks.list(project_id)[:30]]
        return {
            "agent": report.to_dict(),
            "review": {
                "approved": len(approved_ids),
                "total": len(scenes),
                "approved_ids": approved_ids,
                "pending_ids": pending_ids,
            },
            "visual_qa": _read_visual_qa(store, project_id),
            "memory": _memory_status(store, project_id),
            "tasks": tasks,
        }


@router.post("/api/projects/{project_id}/agent/enqueue-next")
def enqueue_next(project_id: str):
    with StudioStore(DB_PATH) as store:
        _project_row(store, project_id)
        try:
            return AgentOne(store, output_root=OUTPUT_ROOT).enqueue_next(project_id)
        except (KeyError, ValueError, RuntimeError) as exc:
            raise HTTPException(400, str(exc)) from exc


@router.post("/api/tasks/{task_id}/run")
def run_queued_task(task_id: str, background_tasks: BackgroundTasks):
    with StudioStore(DB_PATH) as store:
        engine = TaskEngine(store)
        task = engine.get(task_id)
        if task is None:
            raise HTTPException(404, f"Unknown task: {task_id}")
        if task.state != "queued":
            raise HTTPException(400, f"Task {task_id} is {task.state}, expected queued")
        _schedule(background_tasks, task_id)
        return {"scheduled": True, "task": task.to_dict()}


@router.post("/api/tasks/{task_id}/retry")
def retry_task(task_id: str, background_tasks: BackgroundTasks):
    with StudioStore(DB_PATH) as store:
        engine = TaskEngine(store)
        try:
            task = engine.retry(task_id)
        except (KeyError, RuntimeError) as exc:
            raise HTTPException(400, str(exc)) from exc
        _schedule(background_tasks, task_id)
        return {"scheduled": True, "task": task.to_dict()}


@router.post("/api/tasks/{task_id}/cancel")
def cancel_task(task_id: str):
    with StudioStore(DB_PATH) as store:
        engine = TaskEngine(store)
        try:
            task = engine.cancel(task_id)
        except (KeyError, RuntimeError) as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"cancelled": True, "task": task.to_dict()}
