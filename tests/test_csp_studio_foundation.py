from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from csp_studio.import_short import project_from_short
from csp_studio.models import Scene
from csp_studio.shot_director import ShotDirector
from csp_studio.store import StudioStore


class ShotDirectorTests(unittest.TestCase):
    def test_eight_scene_plan_has_establish_reveal_and_twist(self) -> None:
        scenes = [
            Scene(project_id="001", scene_id=i, text=f"scene {i}", prompt="prompt")
            for i in range(1, 9)
        ]
        director = ShotDirector()
        director.plan(scenes)

        self.assertEqual(scenes[0].shot.purpose, "establish")
        self.assertEqual(scenes[-2].shot.purpose, "orientation_reset")
        self.assertEqual(scenes[-1].shot.purpose, "twist")
        self.assertEqual(scenes[-1].shot.camera, "static")
        self.assertTrue(director.audit(scenes).score >= 90)

    def test_audit_detects_repeated_visual_language(self) -> None:
        scenes = [
            Scene(project_id="001", scene_id=i, text="x", prompt="x")
            for i in range(1, 4)
        ]
        for scene in scenes:
            scene.shot.shot_type = "wide"
            scene.shot.camera = "slow_push"
            scene.shot.purpose = "story"
        audit = ShotDirector().audit(scenes)
        self.assertLess(audit.score, 100)
        self.assertTrue(any("powtórzony shot_type" in item for item in audit.warnings))


class StudioStoreTests(unittest.TestCase):
    def test_scene_update_creates_revision(self) -> None:
        payload = {
            "id": "001",
            "title": "Drzwi 0",
            "series": "Nie Otwieraj",
            "fictional": True,
            "status": "ready",
            "narration": "Narracja",
            "visual_style": "documentary",
            "scenes": [
                {
                    "id": 1,
                    "text": "Pierwsza scena",
                    "prompt": "Basement",
                    "continuity_refs": ["basement"],
                    "motion": "push_in",
                }
            ],
        }
        project = project_from_short(payload, "shorts/001-drzwi-0.yaml")

        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "studio.db"
            with StudioStore(db) as store:
                store.upsert_project(project)
                scene = store.get_scene("001", 1)
                self.assertIsNotNone(scene)
                assert scene is not None
                self.assertEqual(scene.revision, 1)

                scene.prompt = "Updated prompt"
                store.upsert_scene(scene, action="edit_prompt", note="manual Studio edit")

                updated = store.get_scene("001", 1)
                self.assertIsNotNone(updated)
                assert updated is not None
                self.assertEqual(updated.revision, 2)
                revisions = store.list_revisions("001", 1)
                self.assertEqual(len(revisions), 1)
                self.assertEqual(revisions[0]["action"], "edit_prompt")
                self.assertEqual(revisions[0]["before"]["prompt"], "Basement")
                self.assertEqual(revisions[0]["after"]["prompt"], "Updated prompt")

    def test_legacy_yaml_fields_survive_import(self) -> None:
        payload = {
            "id": "007",
            "title": "Test",
            "scenes": [
                {
                    "id": 1,
                    "text": "Narration",
                    "prompt": "Prompt",
                    "continuity_refs": ["door", "hall"],
                    "render": {"mode": "crop", "reference_scene": 1},
                    "motion": "slow_push",
                }
            ],
        }
        project = project_from_short(payload)
        scene = project.scenes[0]
        self.assertEqual(scene.continuity_refs, ["door", "hall"])
        self.assertEqual(scene.render["mode"], "crop")
        self.assertEqual(scene.motion, "slow_push")


if __name__ == "__main__":
    unittest.main()
