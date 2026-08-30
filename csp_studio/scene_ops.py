from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path
from typing import Any

from PIL import Image

from .asset_manager import AssetManager, VALID_SCENE_STATUSES
from .models import Asset
from .pipeline_state import invalidate_after_image_change
from .store import StudioStore
from .task_engine import TaskEngine


SUPPORTED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}


class SceneOperations:
    """High-level operations used by the future CSP Studio Scene Editor.

    The existing renderer still reads ``images/scene-XX.png``. Studio keeps immutable
    revision files under ``images/revisions`` and refreshes the canonical renderer
    file whenever a replacement is activated.
    """

    def __init__(self, store: StudioStore, images_dir: str | Path):
        self.store = store
        self.assets = AssetManager(store)
        self.images_dir = Path(images_dir).expanduser().resolve()
        self.revisions_dir = self.images_dir / "revisions"
        self.images_dir.mkdir(parents=True, exist_ok=True)
        self.revisions_dir.mkdir(parents=True, exist_ok=True)

    def canonical_path(self, scene_id: int) -> Path:
        return self.images_dir / f"scene-{scene_id:02d}.png"

    def replace_image(
        self,
        project_id: str,
        scene_id: int,
        source_path: str | Path,
        *,
        source: str = "gpt-browser-manual",
        note: str = "",
    ) -> Asset:
        scene = self.store.get_scene(project_id, scene_id)
        if scene is None:
            raise KeyError(f"Unknown scene {project_id}:{scene_id}")

        incoming = Path(source_path).expanduser().resolve()
        if not incoming.is_file():
            raise FileNotFoundError(incoming)
        if incoming.suffix.lower() not in SUPPORTED_IMAGE_EXTENSIONS:
            raise ValueError(
                f"Unsupported image extension: {incoming.suffix}. "
                f"Supported: {', '.join(sorted(SUPPORTED_IMAGE_EXTENSIONS))}"
            )

        canonical = self.canonical_path(scene_id)
        if incoming == canonical.resolve():
            raise ValueError(
                "Replacement source cannot be the active canonical scene-XX.png. "
                "Use a separate downloaded/generated file."
            )

        active = self.assets.active_asset(project_id, scene_id, "image")
        self._archive_canonical_if_needed(active, canonical)

        revision = self.assets.next_revision(project_id, scene_id, "image")
        revision_path = self.revisions_dir / f"scene-{scene_id:02d}-r{revision}.png"
        self._write_png(incoming, revision_path)

        shutil.copy2(revision_path, canonical)

        metadata = {
            "canonical_path": str(canonical),
            "imported_from": str(incoming),
            "operation": "import-replace",
        }
        if note:
            metadata["note"] = note

        asset = self.assets.register_asset(
            project_id,
            scene_id,
            revision_path,
            kind="image",
            source=source,
            status="candidate",
            metadata=metadata,
            activate=True,
        )
        invalidate_after_image_change(
            TaskEngine(self.store),
            project_id,
            scene_id=scene_id,
            reason=f"scene {scene_id} image revision changed to r{asset.revision}",
        )
        return asset

    def approve(self, project_id: str, scene_id: int, note: str = "") -> None:
        self.assets.approve_scene(project_id, scene_id, note)

    def mark_for_regeneration(self, project_id: str, scene_id: int, note: str = "") -> None:
        self.assets.mark_for_regeneration(project_id, scene_id, note)

    def set_status(self, project_id: str, scene_id: int, status: str, note: str = "") -> None:
        self.assets.set_scene_status(project_id, scene_id, status, note)

    def describe(self, project_id: str, scene_id: int) -> dict[str, Any]:
        scene = self.store.get_scene(project_id, scene_id)
        if scene is None:
            raise KeyError(f"Unknown scene {project_id}:{scene_id}")
        active = self.assets.active_asset(project_id, scene_id, "image")
        return {
            "project_id": project_id,
            "scene_id": scene_id,
            "scene_revision": scene.revision,
            "status": scene.status,
            "text": scene.text,
            "shot": scene.shot.to_dict(),
            "canonical_path": str(self.canonical_path(scene_id)),
            "active_asset": active.to_dict() if active else None,
        }

    def history(self, project_id: str, scene_id: int) -> dict[str, Any]:
        return {
            "assets": [asset.to_dict() for asset in self.assets.list_assets(project_id, scene_id)],
            "scene_revisions": self.store.list_revisions(project_id, scene_id),
        }

    def _archive_canonical_if_needed(self, active: Asset | None, canonical: Path) -> None:
        if active is None or active.asset_id is None:
            return

        active_path = Path(active.path).expanduser().resolve()
        if active_path != canonical.resolve():
            return
        if not canonical.is_file():
            raise FileNotFoundError(
                f"Studio marks the canonical image active but the file is missing: {canonical}"
            )

        archive_path = self.revisions_dir / f"scene-{active.scene_id:02d}-r{active.revision}.png"
        if not archive_path.exists():
            self._write_png(canonical, archive_path)
        self.assets.relocate_asset(
            active.asset_id,
            archive_path,
            metadata_update={
                "archived_from": str(canonical),
                "canonical_path": str(canonical),
            },
        )

    @staticmethod
    def _write_png(source: Path, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.resolve() == destination.resolve():
            return
        if source.suffix.lower() == ".png":
            shutil.copy2(source, destination)
            return
        with Image.open(source) as image:
            if image.mode not in {"RGB", "RGBA"}:
                image = image.convert("RGB")
            image.save(destination, format="PNG")


def _default_db() -> Path:
    output_root = Path(os.getenv("CSP_OUTPUT_DIR", "output")).expanduser()
    return output_root / "csp-studio.db"


def _print_description(data: dict[str, Any]) -> None:
    print(f"SCENE: {data['project_id']}:{data['scene_id']:02d}")
    print(f"STATUS: {data['status']}")
    print(f"SCENE REVISION: r{data['scene_revision']}")
    active = data["active_asset"]
    if active:
        print(f"ACTIVE IMAGE: r{active['revision']} | {active['source']} | {active['path']}")
    else:
        print("ACTIVE IMAGE: none")
    print(f"CANONICAL: {data['canonical_path']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="CSP Studio per-scene operations.")
    parser.add_argument("--db", default=None, help="Studio SQLite path")
    parser.add_argument("--images-dir", required=True, help="Renderer images directory, e.g. C:\\CSP\\output\\001-drzwi-0\\images")
    sub = parser.add_subparsers(dest="command", required=True)

    show = sub.add_parser("show", help="Show scene state and active image")
    show.add_argument("project_id")
    show.add_argument("scene_id", type=int)

    replace = sub.add_parser("replace", aliases=["import-replace"], help="Import an external image and replace one scene")
    replace.add_argument("project_id")
    replace.add_argument("scene_id", type=int)
    replace.add_argument("file")
    replace.add_argument("--source", default="gpt-browser-manual")
    replace.add_argument("--note", default="")

    approve = sub.add_parser("approve", help="Approve the active image for one scene")
    approve.add_argument("project_id")
    approve.add_argument("scene_id", type=int)
    approve.add_argument("--note", default="")

    regen = sub.add_parser("regenerate", help="Mark one scene for regeneration")
    regen.add_argument("project_id")
    regen.add_argument("scene_id", type=int)
    regen.add_argument("--note", default="")

    status = sub.add_parser("status", help="Set one scene status")
    status.add_argument("project_id")
    status.add_argument("scene_id", type=int)
    status.add_argument("value", choices=sorted(VALID_SCENE_STATUSES))
    status.add_argument("--note", default="")

    history = sub.add_parser("history", help="Show asset and scene revision history")
    history.add_argument("project_id")
    history.add_argument("scene_id", type=int)

    args = parser.parse_args()
    db_path = Path(args.db).expanduser() if args.db else _default_db()

    with StudioStore(db_path) as store:
        ops = SceneOperations(store, args.images_dir)
        if args.command == "show":
            _print_description(ops.describe(args.project_id, args.scene_id))
            return
        if args.command in {"replace", "import-replace"}:
            asset = ops.replace_image(args.project_id, args.scene_id, args.file, source=args.source, note=args.note)
            print(f"REPLACED scene {args.scene_id:02d}: image r{asset.revision}")
            _print_description(ops.describe(args.project_id, args.scene_id))
            return
        if args.command == "approve":
            ops.approve(args.project_id, args.scene_id, args.note)
            _print_description(ops.describe(args.project_id, args.scene_id))
            return
        if args.command == "regenerate":
            ops.mark_for_regeneration(args.project_id, args.scene_id, args.note)
            _print_description(ops.describe(args.project_id, args.scene_id))
            return
        if args.command == "status":
            ops.set_status(args.project_id, args.scene_id, args.value, args.note)
            _print_description(ops.describe(args.project_id, args.scene_id))
            return
        if args.command == "history":
            print(json.dumps(ops.history(args.project_id, args.scene_id), ensure_ascii=False, indent=2))
            return


if __name__ == "__main__":
    main()
