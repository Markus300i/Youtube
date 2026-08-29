from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from csp_studio.asset_manager import AssetManager
from csp_studio.import_short import project_from_short
from csp_studio.store import StudioStore


class AssetManagerTests(unittest.TestCase):
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

    def test_register_and_replace_asset_keeps_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with StudioStore(Path(tmp) / "studio.db") as store:
                store.upsert_project(self.project())
                manager = AssetManager(store)

                first = manager.register_asset("001", 1, "scene-01-v1.png")
                second = manager.register_asset("001", 1, "scene-01-v2.png", source="gpt-image")

                self.assertEqual(first.revision, 1)
                self.assertEqual(second.revision, 2)
                assets = manager.list_assets("001", 1)
                self.assertEqual(len(assets), 2)
                self.assertTrue(assets[0].active)
                self.assertFalse(assets[1].active)
                active = manager.active_asset("001", 1)
                assert active is not None
                self.assertEqual(active.path, "scene-01-v2.png")

                scene = store.get_scene("001", 1)
                assert scene is not None
                self.assertEqual(scene.asset_path, "scene-01-v2.png")
                self.assertEqual(scene.status, "generated")
                self.assertEqual(scene.revision, 3)

    def test_regenerate_and_approve_scene(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with StudioStore(Path(tmp) / "studio.db") as store:
                store.upsert_project(self.project())
                manager = AssetManager(store)
                manager.register_asset("001", 1, "scene-01.png")

                manager.mark_for_regeneration("001", 1, "face quality")
                scene = store.get_scene("001", 1)
                assert scene is not None
                self.assertEqual(scene.status, "needs_regeneration")

                manager.register_asset("001", 1, "scene-01-fixed.png", source="gpt-image")
                manager.approve_scene("001", 1)
                scene = store.get_scene("001", 1)
                assert scene is not None
                self.assertEqual(scene.status, "approved")
                self.assertEqual(scene.asset_path, "scene-01-fixed.png")

    def test_cannot_approve_without_asset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with StudioStore(Path(tmp) / "studio.db") as store:
                store.upsert_project(self.project())
                manager = AssetManager(store)
                with self.assertRaises(RuntimeError):
                    manager.approve_scene("001", 2)

    def test_invalid_status_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with StudioStore(Path(tmp) / "studio.db") as store:
                store.upsert_project(self.project())
                manager = AssetManager(store)
                with self.assertRaises(ValueError):
                    manager.set_scene_status("001", 1, "magic")


if __name__ == "__main__":
    unittest.main()
