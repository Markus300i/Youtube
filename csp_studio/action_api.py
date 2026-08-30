from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import unicodedata
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse

from .agent_one import AgentOne
from .scene_ops import SceneOperations
from .store import StudioStore
from .task_engine import TaskEngine
from .task_runner import StudioTaskRunner, run_task

ROOT = Path(__file__).resolve().parents[1]
WEB_DIR = Path(__file__).resolve().parent / "web"
OUTPUT_ROOT = Path(os.getenv("CSP_OUTPUT_DIR", str(ROOT / "output"))).expanduser().resolve()
DB_PATH = Path(os.getenv("CSP_STUDIO_DB", str(OUTPUT_ROOT / "csp-studio.db"))).expanduser().resolve()

router = APIRouter()

MANUAL_ACTIONS: dict[str, tuple[str, str]] = {
    "tts": ("tts", "gpu"),
    "captions": ("captions", "gpu"),
    "sound_design": ("sound_design", "cpu"),
    "visual_qa": ("visual_qa", "network"),
    "opencut_export": ("opencut_export", "io"),
    "render_final": ("render_final", "gpu"),
}

ACTION_META: dict[str, dict[str, Any]] = {
    "tts": {
        "label": "TTS",
        "detail": "Chatterbox narration",
        "check": "tts",
        "requires": (),
    },
    "captions": {
        "label": "Captions",
        "detail": "Whisper subtitles",
        "check": "captions",
        "requires": ("tts",),
    },
    "sound_design": {
        "label": "Sound",
        "detail": "Final audio mix",
        "check": "sound_design",
        "requires": ("tts",),
    },
    "visual_qa": {
        "label": "Visual QA",
        "detail": "NVIDIA visual review",
        "check": "visual_qa",
        "requires": ("active_images",),
    },
    "opencut_export": {
        "label": "OpenCut",
        "detail": "Export interchange",
        "check": "opencut_export",
        "requires": ("active_images", "tts", "captions", "sound_design"),
    },
    "render_final": {
        "label": "Render Final",
        "detail": "FFmpeg final MP4",
        "check": "final_render",
        "requires": ("active_images", "tts", "captions", "sound_design", "scene_review"),
    },
}


def _slug(value: str) -> str:
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-zA-Z0-9]+", "-", ascii_value).strip("-").lower() or "project"


def _project_title(store: StudioStore, project_id: str) -> str:
    row = store.conn.execute("SELECT title FROM projects WHERE project_id=?", (project_id,)).fetchone()
    if row is None:
        raise KeyError(f"Unknown project: {project_id}")
    return str(row["title"])


def _tail(path: Path, chars: int = 1800) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return text[-chars:].replace("\n", " | ")


def _run_logged(command: list[str], log_path: Path, env: dict[str, str]) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8", errors="replace") as log:
        log.write("\n" + "=" * 72 + "\n")
        log.write("COMMAND: " + " ".join(command) + "\n\n")
        log.flush()
        process = subprocess.run(
            command,
            cwd=ROOT,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
        )
        return int(process.returncode)


def _ensure_comfy(log_path: Path, env: dict[str, str]) -> None:
    if os.name != "nt":
        return
    script = ROOT / "setup" / "ensure-comfyui.ps1"
    command = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
    ]
    rc = _run_logged(command, log_path, env)
    if rc != 0:
        raise RuntimeError("ComfyUI could not be started")


def _quick_snapshot(base_snapshot: Path, scene_id: int, target: Path) -> Path:
    payload = yaml.safe_load(base_snapshot.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Invalid Studio snapshot")
    payload["image_model"] = "z-image-turbo"
    matched = False
    for scene in payload.get("scenes") or []:
        if not isinstance(scene, dict) or int(scene.get("id", 0)) != scene_id:
            continue
        scene["render"] = {"mode": "generate"}
        matched = True
        break
    if not matched:
        raise KeyError(f"Scene {scene_id} missing from snapshot")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return target


def _action_statuses(store: StudioStore, project_id: str) -> list[dict[str, Any]]:
    report = AgentOne(store, output_root=OUTPUT_ROOT).inspect(project_id)
    checks = {check.key: check for check in report.checks}
    engine = TaskEngine(store)
    tasks = engine.list(project_id)
    output: list[dict[str, Any]] = []

    for action, (stage, resource) in MANUAL_ACTIONS.items():
        meta = ACTION_META[action]
        stage_tasks = [task for task in tasks if task.stage == stage]
        latest = stage_tasks[0] if stage_tasks else None
        active = next((task for task in stage_tasks if task.state in {"queued", "running"}), None)
        missing = [
            key for key in meta["requires"]
            if key not in checks or not checks[key].ok
        ]
        check = checks.get(str(meta["check"]))

        if active is not None:
            state = active.state
        elif missing:
            state = "blocked"
        elif check is not None and check.ok:
            state = "done"
        elif latest is not None and latest.state == "failed":
            state = "failed"
        else:
            state = "ready"

        output.append(
            {
                "action": action,
                "stage": stage,
                "resource": resource,
                "label": meta["label"],
                "detail": meta["detail"],
                "state": state,
                "can_run": not missing and active is None,
                "requirements": list(meta["requires"]),
                "missing_requirements": missing,
                "missing_labels": [checks[key].label if key in checks else key for key in missing],
                "check_ok": bool(check and check.ok),
                "latest_task": latest.to_dict() if latest else None,
                "active_task": active.to_dict() if active else None,
            }
        )
    return output


def run_quick_regenerate(task_id: str) -> dict[str, Any]:
    log_path = OUTPUT_ROOT / ".studio-tasks" / f"{task_id}.log"
    with StudioStore(DB_PATH) as store:
        engine = TaskEngine(store)
        task = engine.claim(task_id, "studio-web-quick")
        if task is None:
            current = engine.get(task_id)
            if current is None:
                raise KeyError(task_id)
            return current.to_dict()
        if task.scene_id is None:
            return engine.fail(task_id, "Quick regenerate requires scene_id").to_dict()
        engine.progress(task_id, 5, stage="prepare")
        title = _project_title(store, task.project_id)
        base_snapshot = StudioTaskRunner(DB_PATH, output_root=OUTPUT_ROOT)._write_snapshot(store, task.project_id)

    try:
        env = os.environ.copy()
        env["CSP_OUTPUT_DIR"] = str(OUTPUT_ROOT)
        env["CSP_STUDIO_DB"] = str(DB_PATH)
        with StudioStore(DB_PATH) as store:
            TaskEngine(store).progress(task_id, 10, stage="ensure_comfyui")
        _ensure_comfy(log_path, env)

        with tempfile.TemporaryDirectory(prefix="csp-studio-quick-") as tmp:
            tmp_root = Path(tmp)
            temp_output = tmp_root / "output"
            quick_yaml = _quick_snapshot(base_snapshot, int(task.scene_id), tmp_root / "quick.yaml")
            quick_env = dict(env)
            quick_env["CSP_OUTPUT_DIR"] = str(temp_output)
            command = [
                sys.executable,
                str(ROOT / "scripts" / "generate_scene.py"),
                str(quick_yaml),
                str(task.scene_id),
            ]
            with StudioStore(DB_PATH) as store:
                TaskEngine(store).progress(task_id, 20, stage="quick_generate")
            rc = _run_logged(command, log_path, quick_env)
            if rc != 0:
                raise RuntimeError(f"Quick scene generator exited with code {rc}")

            generated = temp_output / f"{task.project_id}-{_slug(title)}" / "images" / f"scene-{int(task.scene_id):02d}.png"
            if not generated.is_file() or generated.stat().st_size <= 0:
                raise FileNotFoundError(generated)

            with StudioStore(DB_PATH) as store:
                engine = TaskEngine(store)
                engine.progress(task_id, 85, stage="activate_revision")
                images_dir = OUTPUT_ROOT / f"{task.project_id}-{_slug(title)}" / "images"
                asset = SceneOperations(store, images_dir).replace_image(
                    task.project_id,
                    int(task.scene_id),
                    generated,
                    source="local-zimage-quick",
                    note="Quick Regenerate from CSP Studio",
                )
                engine.set_checkpoint(
                    task.project_id,
                    "visual_qa",
                    "stale",
                    metadata={"reason": f"scene {task.scene_id} quick regenerated"},
                )
                result = {
                    "asset": asset.to_dict(),
                    "mode": "quick",
                    "model": "z-image-turbo",
                    "log_path": str(log_path),
                }
                return engine.complete(task_id, result).to_dict()
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}"
        tail = _tail(log_path)
        if tail:
            detail += f" | log: {tail}"
        with StudioStore(DB_PATH) as store:
            engine = TaskEngine(store)
            current = engine.get(task_id)
            if current and current.state == "cancelled":
                return current.to_dict()
            return engine.fail(task_id, detail).to_dict()


@router.get("/actions.js")
def actions_js():
    return FileResponse(WEB_DIR / "actions.js", media_type="application/javascript")


@router.get("/api/projects/{project_id}/actions")
def action_status(project_id: str):
    with StudioStore(DB_PATH) as store:
        _project_title(store, project_id)
        return {"project_id": project_id, "actions": _action_statuses(store, project_id)}


@router.post("/api/projects/{project_id}/scenes/{scene_id}/quick-regenerate")
def quick_regenerate(project_id: str, scene_id: int, background_tasks: BackgroundTasks):
    with StudioStore(DB_PATH) as store:
        if store.get_scene(project_id, scene_id) is None:
            raise HTTPException(404, f"Unknown scene: {project_id}:{scene_id}")
        engine = TaskEngine(store)
        active = next(
            (
                task for task in engine.list(project_id)
                if task.stage == "regenerate_image_quick"
                and task.scene_id == scene_id
                and task.state in {"queued", "running"}
            ),
            None,
        )
        if active is not None:
            return {"scheduled": False, "reason": "already_active", "task": active.to_dict()}
        task = engine.submit(
            project_id,
            "regenerate_image_quick",
            scene_id=scene_id,
            resource="gpu",
            payload={"mode": "quick", "model": "z-image-turbo"},
        )
    background_tasks.add_task(run_quick_regenerate, task.task_id)
    return {"scheduled": True, "task": task.to_dict()}


@router.post("/api/projects/{project_id}/actions/{action}")
def manual_action(project_id: str, action: str, background_tasks: BackgroundTasks):
    mapping = MANUAL_ACTIONS.get(action)
    if mapping is None:
        raise HTTPException(400, f"Unsupported manual action: {action}")
    stage, resource = mapping

    with StudioStore(DB_PATH) as store:
        _project_title(store, project_id)
        statuses = {item["action"]: item for item in _action_statuses(store, project_id)}
        status = statuses[action]
        if status["missing_requirements"]:
            raise HTTPException(
                409,
                "Brak wymaganych etapów: " + ", ".join(status["missing_labels"]),
            )
        if status["active_task"] is not None:
            return {
                "scheduled": False,
                "reason": "already_active",
                "task": status["active_task"],
                "action": status,
            }

        task = TaskEngine(store).submit(
            project_id,
            stage,
            resource=resource,
            payload={"source": "manual-actions-panel"},
        )

    background_tasks.add_task(run_task, task.task_id, db_path=DB_PATH, output_root=OUTPUT_ROOT)
    return {"scheduled": True, "task": task.to_dict(), "action": status}
