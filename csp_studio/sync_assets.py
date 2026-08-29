from __future__ import annotations

import argparse
import os
from pathlib import Path

from .asset_manager import AssetManager
from .store import StudioStore


def main() -> None:
    parser = argparse.ArgumentParser(description="Register existing CSP scene assets in Studio SQLite.")
    parser.add_argument("project_id")
    parser.add_argument("images_dir")
    parser.add_argument("--db", default=None)
    parser.add_argument("--source", default="existing-pipeline")
    args = parser.parse_args()

    output_root = Path(os.getenv("CSP_OUTPUT_DIR", "output")).expanduser()
    db_path = Path(args.db) if args.db else output_root / "csp-studio.db"
    images_dir = Path(args.images_dir).expanduser()

    if not images_dir.exists():
        raise FileNotFoundError(images_dir)

    registered = 0
    skipped = 0
    with StudioStore(db_path) as store:
        manager = AssetManager(store)
        scenes = store.list_scenes(args.project_id)
        if not scenes:
            raise RuntimeError(f"Project has no scenes in Studio DB: {args.project_id}")

        for scene in scenes:
            path = images_dir / f"scene-{scene.scene_id:02d}.png"
            if not path.exists():
                print(f"MISSING scene {scene.scene_id:02d}: {path}")
                skipped += 1
                continue

            active = manager.active_asset(args.project_id, scene.scene_id, "image")
            if active and Path(active.path) == path:
                print(f"SKIP scene {scene.scene_id:02d}: already active")
                skipped += 1
                continue

            asset = manager.register_asset(
                args.project_id,
                scene.scene_id,
                path,
                kind="image",
                source=args.source,
                status="candidate",
                activate=True,
                metadata={"imported_from": "existing scene PNG"},
            )
            print(
                f"REGISTERED scene {scene.scene_id:02d}: "
                f"image r{asset.revision} -> {asset.path}"
            )
            registered += 1

    print(f"STUDIO DB: {db_path}")
    print(f"REGISTERED: {registered}")
    print(f"SKIPPED/MISSING: {skipped}")


if __name__ == "__main__":
    main()
