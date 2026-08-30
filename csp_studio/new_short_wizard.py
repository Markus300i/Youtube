from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import yaml

from .import_short import project_from_short
from .store import StudioStore

PROJECT_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")


class WizardValidationError(ValueError):
    pass


def validate_wizard_payload(payload: dict[str, Any]) -> None:
    project_id = str(payload.get("id") or "").strip()
    title = str(payload.get("title") or "").strip()
    narration = str(payload.get("narration") or "").strip()
    scenes = payload.get("scenes")

    if not PROJECT_ID_RE.fullmatch(project_id):
        raise WizardValidationError("id must be a stable 1-64 character slug")
    if not title:
        raise WizardValidationError("title is required")
    if payload.get("fictional") is not True:
        raise WizardValidationError("fictional must be true for CSP projects")
    if not isinstance(scenes, list) or len(scenes) != 8:
        raise WizardValidationError("CSP Short requires exactly 8 scenes")

    ids = []
    combined_text: list[str] = []
    for raw in scenes:
        if not isinstance(raw, dict):
            raise WizardValidationError("each scene must be an object")
        scene_id = int(raw.get("id", 0))
        ids.append(scene_id)
        text = str(raw.get("text") or "").strip()
        prompt = str(raw.get("prompt") or "").strip()
        if not text:
            raise WizardValidationError(f"scene {scene_id}: text is required")
        if not prompt:
            raise WizardValidationError(f"scene {scene_id}: prompt is required")
        combined_text.append(text)
    if ids != list(range(1, 9)):
        raise WizardValidationError("scene ids must be exactly 1..8 in order")

    if not narration:
        narration = " ".join(combined_text)
        payload["narration"] = narration
    words = narration.split()
    if len(words) < 70 or len(words) > 160:
        raise WizardValidationError(f"narration must contain 70-160 words, got {len(words)}")

    scene_words = " ".join(combined_text).split()
    if not scene_words:
        raise WizardValidationError("scene narration is empty")


def normalize_wizard_payload(payload: dict[str, Any]) -> dict[str, Any]:
    data = dict(payload)
    data.setdefault("series", "Ciemna Strona Polski")
    data["fictional"] = True
    data.setdefault("status", "draft")
    data.setdefault(
        "visual_style",
        "realistyczny polski thriller dokumentalny, cinematic, naturalne światło, desaturated colors, subtle film grain, photorealistic, 9:16",
    )
    scenes: list[dict[str, Any]] = []
    for raw in data.get("scenes") or []:
        scene = dict(raw)
        scene.setdefault("motion", "static")
        scene.setdefault("continuity_refs", [])
        scene.setdefault("render", {"mode": "generate"})
        scene.setdefault(
            "shot",
            {
                "shot_type": "medium",
                "camera": "static",
                "purpose": "story",
                "visual_anchor": None,
                "motion_intensity": "low",
            },
        )
        scenes.append(scene)
    data["scenes"] = scenes
    validate_wizard_payload(data)
    return data


class NewShortWizard:
    """Create a canonical CSP project from a reviewed structured draft.

    V1 intentionally does not let an LLM write directly to SQLite. A provider can
    prepare the JSON/YAML draft, but this deterministic validator is the gate that
    creates the canonical project and compatibility source YAML.
    """

    def __init__(self, store: StudioStore, *, shorts_dir: str | Path):
        self.store = store
        self.shorts_dir = Path(shorts_dir).expanduser().resolve()
        self.shorts_dir.mkdir(parents=True, exist_ok=True)

    def create(self, payload: dict[str, Any], *, overwrite_source: bool = False) -> dict[str, Any]:
        data = normalize_wizard_payload(payload)
        project_id = str(data["id"])
        target = self.shorts_dir / f"{project_id}.yaml"
        if target.exists() and not overwrite_source:
            raise FileExistsError(target)
        temp = target.with_suffix(".yaml.tmp")
        temp.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
        temp.replace(target)
        project = project_from_short(data, str(target))
        self.store.upsert_project(project)
        return {
            "project": project.to_dict(),
            "source_yaml": str(target),
            "scene_count": len(project.scenes),
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a CSP Studio project from a reviewed wizard JSON/YAML draft")
    parser.add_argument("draft")
    parser.add_argument("--db", required=True)
    parser.add_argument("--shorts-dir", default="shorts")
    parser.add_argument("--overwrite-source", action="store_true")
    args = parser.parse_args()
    source = Path(args.draft)
    if source.suffix.lower() == ".json":
        payload = json.loads(source.read_text(encoding="utf-8"))
    else:
        payload = yaml.safe_load(source.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit("Wizard draft must be an object")
    with StudioStore(args.db) as store:
        result = NewShortWizard(store, shorts_dir=args.shorts_dir).create(payload, overwrite_source=args.overwrite_source)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
