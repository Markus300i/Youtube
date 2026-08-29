from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from .asset_manager import AssetManager
from .store import StudioStore

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_ROOT = Path(os.getenv("CSP_OUTPUT_DIR", str(ROOT / "output"))).expanduser().resolve()
DB_PATH = Path(os.getenv("CSP_STUDIO_DB", str(OUTPUT_ROOT / "csp-studio.db"))).expanduser().resolve()
FORMAT_VERSION = "csp-opencut-interchange/1"


def _slug(value: str) -> str:
    import re
    import unicodedata

    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-zA-Z0-9]+", "-", ascii_value).strip("-").lower() or "project"


def _project_row(store: StudioStore, project_id: str):
    row = store.conn.execute("SELECT * FROM projects WHERE project_id=?", (project_id,)).fetchone()
    if row is None:
        raise KeyError(f"Unknown project: {project_id}")
    return row


def _project_dir(store: StudioStore, project_id: str) -> Path:
    row = _project_row(store, project_id)
    return OUTPUT_ROOT / f"{project_id}-{_slug(row['title'])}"


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(path)
    return json.loads(path.read_text(encoding="utf-8"))


def _motion_hint(motion: str, intensity: str) -> dict[str, Any]:
    """Return editor-neutral motion intent, not OpenCut private implementation details."""
    amount = {"none": 0.0, "low": 0.025, "medium": 0.045, "high": 0.07}.get(intensity, 0.025)
    normalized = (motion or "static").lower()
    if normalized in {"static", "none"}:
        return {"kind": "static", "intensity": intensity}
    if normalized in {"push_in", "slow_push"}:
        return {
            "kind": "transform",
            "intensity": intensity,
            "from": {"scale": 1.0, "x": 0.0, "y": 0.0},
            "to": {"scale": round(1.0 + amount, 4), "x": 0.0, "y": 0.0},
        }
    if normalized == "slow_pull":
        return {
            "kind": "transform",
            "intensity": intensity,
            "from": {"scale": round(1.0 + amount, 4), "x": 0.0, "y": 0.0},
            "to": {"scale": 1.0, "x": 0.0, "y": 0.0},
        }
    if normalized in {"pan_left", "pan_right"}:
        direction = -1.0 if normalized == "pan_left" else 1.0
        return {
            "kind": "transform",
            "intensity": intensity,
            "from": {"scale": round(1.0 + amount, 4), "x": round(-direction * amount, 4), "y": 0.0},
            "to": {"scale": round(1.0 + amount, 4), "x": round(direction * amount, 4), "y": 0.0},
        }
    return {"kind": "intent", "name": normalized, "intensity": intensity}


def build_manifest(store: StudioStore, project_id: str) -> dict[str, Any]:
    project = _project_row(store, project_id)
    project_dir = _project_dir(store, project_id)
    images = AssetManager(store)
    scenes = store.list_scenes(project_id)
    if not scenes:
        raise ValueError(f"Project {project_id} has no scenes")

    timing_path = project_dir / "audio" / "tts-timings.json"
    timings = _read_json(timing_path)
    timing_by_id = {int(item["id"]): item for item in timings.get("scenes", [])}

    video_elements: list[dict[str, Any]] = []
    warnings: list[str] = []
    for scene in scenes:
        active = images.active_asset(project_id, scene.scene_id, "image")
        if active is None:
            raise RuntimeError(f"Scene {scene.scene_id:02d} has no active image asset")
        asset_path = Path(active.path)
        if not asset_path.is_file():
            raise FileNotFoundError(asset_path)
        timing = timing_by_id.get(scene.scene_id)
        if timing is None:
            raise RuntimeError(f"Scene {scene.scene_id:02d} missing from {timing_path}")

        start = float(timing["start"])
        end = float(timing["end"])
        duration = float(timing.get("duration", end - start))
        if duration <= 0:
            raise ValueError(f"Scene {scene.scene_id:02d} has invalid duration {duration}")

        video_elements.append(
            {
                "id": f"scene-{scene.scene_id:02d}",
                "type": "image",
                "name": f"Scene {scene.scene_id:02d}",
                "source_path": str(asset_path.resolve()),
                "source_revision": active.revision,
                "source_provider": active.source,
                "start_time": round(start, 4),
                "duration": round(duration, 4),
                "end_time": round(end, 4),
                "trim_start": 0.0,
                "trim_end": 0.0,
                "shot_intent": scene.shot.to_dict(),
                "motion_intent": _motion_hint(scene.motion or scene.shot.camera, scene.shot.motion_intensity),
                "scene_status": scene.status,
                "scene_revision": scene.revision,
                "narration": scene.text,
            }
        )
        if scene.status not in {"approved", "render_ready"}:
            warnings.append(f"Scene {scene.scene_id:02d} status is {scene.status}")

    total_duration = float(timings.get("duration") or video_elements[-1]["end_time"])
    voice = project_dir / "audio" / "voice.wav"
    final_mix = project_dir / "audio" / "final_mix.wav"
    subtitles_ass = project_dir / "subtitles.ass"
    subtitles_srt = project_dir / "subtitles.srt"

    audio_tracks: list[dict[str, Any]] = []
    if final_mix.is_file():
        audio_tracks.append(
            {
                "id": "audio-master",
                "name": "CSP Final Mix",
                "role": "master",
                "source_path": str(final_mix.resolve()),
                "start_time": 0.0,
                "duration": round(total_duration, 4),
            }
        )
    elif voice.is_file():
        audio_tracks.append(
            {
                "id": "narrator",
                "name": "Narrator",
                "role": "narrator",
                "source_path": str(voice.resolve()),
                "start_time": 0.0,
                "duration": round(total_duration, 4),
            }
        )
        warnings.append("final_mix.wav missing; exported narrator only")
    else:
        warnings.append("No final_mix.wav or voice.wav found")

    caption_sources = []
    for kind, path in (("ass", subtitles_ass), ("srt", subtitles_srt)):
        if path.is_file():
            caption_sources.append({"format": kind, "source_path": str(path.resolve())})

    return {
        "format": FORMAT_VERSION,
        "target": {
            "name": "OpenCut",
            "strategy": "adapter-contract",
            "note": "Use current/future OpenCut Editor API to materialize this manifest; do not rely on private project serialization.",
        },
        "project": {
            "id": project_id,
            "name": project["title"],
            "series": project["series"],
            "fictional": bool(project["fictional"]),
            "canvas": {"width": 1080, "height": 1920},
            "fps": 30,
            "duration": round(total_duration, 4),
        },
        "timeline": {
            "main_video": {
                "id": "csp-main-video",
                "type": "video",
                "elements": video_elements,
            },
            "audio": audio_tracks,
            "captions": caption_sources,
        },
        "compatibility": {
            "opencut_classic_model": "TProject -> TScene -> SceneTracks -> ImageElement/AudioElement/TextTrack",
            "required_materialization": [
                "import external source_path files as OpenCut media and obtain mediaId",
                "create one main video track with ordered image elements",
                "translate motion_intent to OpenCut transform animations/keyframes",
                "import master audio or narrator track",
                "import captions through OpenCut subtitle/editor API when available",
            ],
        },
        "warnings": warnings,
    }


def export_manifest(store: StudioStore, project_id: str, output: str | Path | None = None) -> Path:
    manifest = build_manifest(store, project_id)
    project_dir = _project_dir(store, project_id)
    target = Path(output).expanduser() if output else project_dir / "opencut" / "csp-opencut.json"
    target = target.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(target.suffix + ".tmp")
    temp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, target)
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description="Export CSP Studio project to OpenCut adapter contract")
    parser.add_argument("project_id")
    parser.add_argument("--output")
    args = parser.parse_args()

    with StudioStore(DB_PATH) as store:
        path = export_manifest(store, args.project_id, args.output)
        manifest = json.loads(path.read_text(encoding="utf-8"))
        print(f"OPENCUT MANIFEST: {path}")
        print(f"CLIPS: {len(manifest['timeline']['main_video']['elements'])}")
        print(f"DURATION: {manifest['project']['duration']:.2f}s")
        for warning in manifest.get("warnings", []):
            print(f"WARN: {warning}")


if __name__ == "__main__":
    main()
