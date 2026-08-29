from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image

from csp_studio.asset_manager import AssetManager
from csp_studio.import_short import project_from_short
from csp_studio.scene_ops import SceneOperations
from csp_studio.store import StudioStore


def make_png(path: Path, rgb: tuple[int, int, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (32, 48), rgb).save(path, format="PNG")


class SceneOperationsTests(unittest.TestCase):
    @staticmethod
    def project():
        return project_from_short(
            {
                "id": "001",
                "title": "Drzwi 0",
                "scenes": [
                    {"id": 1, "text": "Scene one", "prompt": "Door"},
                    {"id": 2, "text": "Scene two", "prompt": "Detail"},
                ],
            }
        )

    def test_replace_archives_old_canonical_and_updates_renderer_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            images = root / "images"
            canonical = images / "scene-01.png"
            incoming = root / "downloads" / "new-scene.png"
            make_png(canonical, (10, 20, 30))
            make_png(incoming, (200, 210, 220))

            with StudioStore(root / "studio.db") as store:
                store.upsert_project(self.project())
                manager = AssetManager(store)
                first = manager.register_asset(
                    "001",
                    1,
                    canonical,
                    source="gpt-browser-manual",
                )
                self.assertEqual(first.revision, 1)

                ops = SceneOperations(store, images)
                second = ops.replace_image(
                    "001",
                    1,
                    incoming,
                    source="gpt-browser-manual",
                    note="better administrator shot",
                )

                self.assertEqual(second.revision, 2)
                self.assertTrue(canonical.exists())
                self.assertEqual(Image.open(canonical).getpixel((0, 0)), (200, 210, 220))

                assets = manager.list_assets("001", 1, "image")
                self.assertEqual(len(assets), 2)
                old = next(item for item in assets if item.revision == 1)
                new = next(item for item in assets if item.revision == 2)
                self.assertFalse(old.active)
                self.assertTrue(new.active)
                self.assertTrue(Path(old.path).name.endswith("scene-01-r1.png"))
                self.assertTrue(Path(new.path).name.endswith("scene-01-r2.png"))
                self.assertTrue(Path(old.path).exists())
                self.assertTrue(Path(new.path).exists())
                self.assertEqual(Image.open(Path(old.path)).getpixel((0, 0)), (10, 20, 30))
                self.assertEqual(Image.open(Path(new.path)).getpixel((0, 0)), (200, 210, 220))

                scene = store.get_scene("001", 1)
                assert scene is not None
                self.assertEqual(scene.status, "generated")
                self.assertEqual(Path(scene.asset_path or "").name, "scene-01-r2.png")

    def test_replace_converts_jpeg_to_png_revision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            images = root / "images"
            incoming = root / "incoming.jpg"
            incoming.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (20, 20), (90, 80, 70)).save(incoming, format="JPEG")

            with StudioStore(root / "studio.db") as store:
                store.upsert_project(self.project())
                ops = SceneOperations(store, images)
                asset = ops.replace_image("001", 1, incoming)

                self.assertEqual(asset.revision, 1)
                self.assertEqual(Path(asset.path).suffix.lower(), ".png")
                self.assertTrue((images / "scene-01.png").exists())

    def test_replace_rejects_canonical_as_input(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            images = root / "images"
            canonical = images / "scene-01.png"
            make_png(canonical, (1, 2, 3))

            with StudioStore(root / "studio.db") as store:
                store.upsert_project(self.project())
                ops = SceneOperations(store, images)
                with self.assertRaises(ValueError):
                    ops.replace_image("001", 1, canonical)

    def test_show_and_history_report_active_revision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            images = root / "images"
            incoming = root / "new.png"
            make_png(incoming, (1, 2, 3))

            with StudioStore(root / "studio.db") as store:
                store.upsert_project(self.project())
                ops = SceneOperations(store, images)
                ops.replace_image("001", 1, incoming)

                description = ops.describe("001", 1)
                self.assertEqual(description["status"], "generated")
                self.assertEqual(description["active_asset"]["revision"], 1)

                history = ops.history("001", 1)
                self.assertEqual(len(history["assets"]), 1)
                self.assertGreaterEqual(len(history["scene_revisions"]), 1)


if __name__ == "__main__":
    unittest.main()
