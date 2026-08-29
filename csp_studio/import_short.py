from __future__ import annotations

import argparse
import os
from pathlib import Path

import yaml

from .models import Project, Scene
from .shot_director import ShotDirector
from .store import StudioStore

ROOT = Path(__file__).resolve().parents[1]


def project_from_short(short: dict, source_yaml: str | None = None) -> Project:
    project_id = str(short["id"])
    scenes = [
        Scene(
            project_id=project_id,
            scene_id=int(raw["id"]),
            text=str(raw.get("text") or "").strip(),
            prompt=str(raw.get("prompt") or "").strip(),
            continuity_refs=[str(item) for item in (raw.get("continuity_refs") or [])],
            render=dict(raw.get("render") or {}),
            motion=str(raw.get("motion") or "static"),
            status="ready" if str(short.get("status", "")).lower() == "ready" else "draft",
        )
        for raw in short.get("scenes") or []
    ]
    ShotDirector().plan(scenes)
    return Project(
        project_id=project_id,
        title=str(short.get("title") or project_id),
        series=str(short.get("series") or ""),
        fictional=bool(short.get("fictional", True)),
        status=str(short.get("status") or "draft"),
        narration=str(short.get("narration") or "").strip(),
        visual_style=str(short.get("visual_style") or "").strip(),
        source_yaml=source_yaml,
        scenes=scenes,
    )


def load_short(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"Nieprawidłowy YAML: {path}")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Import legacy CSP Short YAML into CSP Studio SQLite.")
    parser.add_argument("short_file", help="Path to shorts/*.yaml")
    parser.add_argument("--db", help="SQLite path. Defaults to CSP_OUTPUT_DIR/csp-studio.db")
    args = parser.parse_args()

    short_path = Path(args.short_file)
    if not short_path.is_absolute():
        short_path = ROOT / short_path
    short_path = short_path.resolve()
    if not short_path.exists():
        raise FileNotFoundError(short_path)

    output_root = Path(os.getenv("CSP_OUTPUT_DIR", str(ROOT / "output"))).expanduser()
    if not output_root.is_absolute():
        output_root = (ROOT / output_root).resolve()
    db_path = Path(args.db).expanduser() if args.db else output_root / "csp-studio.db"
    if not db_path.is_absolute():
        db_path = (ROOT / db_path).resolve()

    project = project_from_short(load_short(short_path), str(short_path))
    director = ShotDirector()
    audit = director.audit(project.scenes)

    with StudioStore(db_path) as store:
        store.upsert_project(project)

    print(f"STUDIO DB: {db_path}")
    print(f"PROJECT: {project.project_id} - {project.title}")
    print(f"SCENES: {len(project.scenes)}")
    print(f"SHOT SCORE: {audit.score}/100")
    if audit.warnings:
        for warning in audit.warnings:
            print(f"WARN: {warning}")
    else:
        print("SHOT DIRECTOR: OK")


if __name__ == "__main__":
    main()
