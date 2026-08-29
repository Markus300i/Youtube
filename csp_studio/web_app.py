from __future__ import annotations

import os
import re
import shutil
import unicodedata
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from .asset_manager import AssetManager, VALID_SCENE_STATUSES
from .scene_ops import SUPPORTED_IMAGE_EXTENSIONS, SceneOperations
from .store import StudioStore

ROOT = Path(__file__).resolve().parents[1]
WEB_DIR = Path(__file__).resolve().parent / "web"
OUTPUT_ROOT = Path(os.getenv("CSP_OUTPUT_DIR", str(ROOT / "output"))).expanduser().resolve()
DB_PATH = Path(os.getenv("CSP_STUDIO_DB", str(OUTPUT_ROOT / "csp-studio.db"))).expanduser().resolve()
INCOMING_DIR = OUTPUT_ROOT / ".studio-incoming"

app = FastAPI(title="CSP Studio", version="0.1.0")


def slugify(value: str) -> str:
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-zA-Z0-9]+", "-", ascii_value).strip("-").lower() or "project"


def _project_row(store: StudioStore, project_id: str):
    row = store.conn.execute(
        "SELECT * FROM projects WHERE project_id=?",
        (project_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(404, f"Unknown project: {project_id}")
    return row


def _images_dir(store: StudioStore, project_id: str) -> Path:
    row = _project_row(store, project_id)
    return OUTPUT_ROOT / f"{project_id}-{slugify(row['title'])}" / "images"


def _scene_payload(store: StudioStore, project_id: str, scene_id: int) -> dict:
    scene = store.get_scene(project_id, scene_id)
    if scene is None:
        raise HTTPException(404, f"Unknown scene: {project_id}:{scene_id}")
    manager = AssetManager(store)
    active = manager.active_asset(project_id, scene_id, "image")
    return {
        "project_id": project_id,
        "scene_id": scene_id,
        "text": scene.text,
        "prompt": scene.prompt,
        "status": scene.status,
        "scene_revision": scene.revision,
        "shot": scene.shot.to_dict(),
        "motion": scene.motion,
        "continuity_refs": scene.continuity_refs,
        "active_asset": active.to_dict() if active else None,
        "image_url": f"/api/projects/{project_id}/scenes/{scene_id}/image?v={scene.revision}",
    }


@app.get("/")
def index():
    return FileResponse(WEB_DIR / "index.html", media_type="text/html")


@app.get("/app.js")
def app_js():
    return FileResponse(WEB_DIR / "app.js", media_type="application/javascript")


@app.get("/styles.css")
def styles_css():
    return FileResponse(WEB_DIR / "styles.css", media_type="text/css")


@app.get("/api/health")
def health():
    return {"ok": True, "db": str(DB_PATH), "output_root": str(OUTPUT_ROOT)}


@app.get("/api/projects")
def projects():
    with StudioStore(DB_PATH) as store:
        rows = store.conn.execute(
            "SELECT project_id,title,series,status,updated_at FROM projects ORDER BY updated_at DESC"
        ).fetchall()
        return [dict(row) for row in rows]


@app.get("/api/projects/{project_id}/scenes")
def scenes(project_id: str):
    with StudioStore(DB_PATH) as store:
        _project_row(store, project_id)
        return [_scene_payload(store, project_id, scene.scene_id) for scene in store.list_scenes(project_id)]


@app.get("/api/projects/{project_id}/scenes/{scene_id}")
def scene(project_id: str, scene_id: int):
    with StudioStore(DB_PATH) as store:
        return _scene_payload(store, project_id, scene_id)


@app.get("/api/projects/{project_id}/scenes/{scene_id}/image")
def scene_image(project_id: str, scene_id: int):
    with StudioStore(DB_PATH) as store:
        scene = store.get_scene(project_id, scene_id)
        if scene is None:
            raise HTTPException(404, "Scene not found")
        manager = AssetManager(store)
        active = manager.active_asset(project_id, scene_id, "image")
        path = Path(active.path) if active else _images_dir(store, project_id) / f"scene-{scene_id:02d}.png"
        if not path.is_file():
            raise HTTPException(404, "Scene image not found")
        return FileResponse(path)


@app.get("/api/projects/{project_id}/scenes/{scene_id}/history")
def history(project_id: str, scene_id: int):
    with StudioStore(DB_PATH) as store:
        ops = SceneOperations(store, _images_dir(store, project_id))
        return ops.history(project_id, scene_id)


@app.post("/api/projects/{project_id}/scenes/{scene_id}/replace")
def replace_scene(
    project_id: str,
    scene_id: int,
    file: UploadFile = File(...),
    source: str = Form("gpt-browser-manual"),
    note: str = Form("GUI import/replace"),
):
    suffix = Path(file.filename or "image.png").suffix.lower()
    if suffix not in SUPPORTED_IMAGE_EXTENSIONS:
        raise HTTPException(400, f"Unsupported image extension: {suffix}")

    INCOMING_DIR.mkdir(parents=True, exist_ok=True)
    incoming = INCOMING_DIR / f"{project_id}-scene-{scene_id:02d}-{uuid.uuid4().hex}{suffix}"
    try:
        with incoming.open("wb") as out:
            shutil.copyfileobj(file.file, out)
        with StudioStore(DB_PATH) as store:
            ops = SceneOperations(store, _images_dir(store, project_id))
            asset = ops.replace_image(project_id, scene_id, incoming, source=source, note=note)
            return {"asset": asset.to_dict(), "scene": _scene_payload(store, project_id, scene_id)}
    except (KeyError, ValueError, FileNotFoundError) as exc:
        raise HTTPException(400, str(exc)) from exc
    finally:
        incoming.unlink(missing_ok=True)


@app.post("/api/projects/{project_id}/scenes/{scene_id}/approve")
def approve(project_id: str, scene_id: int, note: str = Form("Approved in CSP Studio GUI")):
    with StudioStore(DB_PATH) as store:
        ops = SceneOperations(store, _images_dir(store, project_id))
        try:
            ops.approve(project_id, scene_id, note)
        except (KeyError, RuntimeError) as exc:
            raise HTTPException(400, str(exc)) from exc
        return _scene_payload(store, project_id, scene_id)


@app.post("/api/projects/{project_id}/scenes/{scene_id}/regenerate")
def regenerate(project_id: str, scene_id: int, note: str = Form("Marked in CSP Studio GUI")):
    with StudioStore(DB_PATH) as store:
        ops = SceneOperations(store, _images_dir(store, project_id))
        try:
            ops.mark_for_regeneration(project_id, scene_id, note)
        except KeyError as exc:
            raise HTTPException(400, str(exc)) from exc
        return _scene_payload(store, project_id, scene_id)


@app.post("/api/projects/{project_id}/scenes/{scene_id}/status")
def set_status(project_id: str, scene_id: int, status: str = Form(...), note: str = Form("")):
    if status not in VALID_SCENE_STATUSES:
        raise HTTPException(400, f"Unsupported status: {status}")
    with StudioStore(DB_PATH) as store:
        ops = SceneOperations(store, _images_dir(store, project_id))
        try:
            ops.set_status(project_id, scene_id, status, note)
        except KeyError as exc:
            raise HTTPException(400, str(exc)) from exc
        return _scene_payload(store, project_id, scene_id)


def main() -> None:
    import uvicorn

    uvicorn.run("csp_studio.web_app:app", host="127.0.0.1", port=8765, reload=False)


if __name__ == "__main__":
    main()
