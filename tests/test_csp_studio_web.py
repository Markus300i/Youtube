from __future__ import annotations

import importlib
import os
import tempfile
import unittest
from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from csp_studio.asset_manager import AssetManager
from csp_studio.import_short import project_from_short
from csp_studio.store import StudioStore


class StudioWebTests(unittest.TestCase):
    def test_gui_edits_scene_plan_and_replaces_one_image(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "output"
            output.mkdir()
            db = output / "csp-studio.db"
            images = output / "001-drzwi-0" / "images"
            images.mkdir(parents=True)

            project = project_from_short(
                {
                    "id": "001",
                    "title": "Drzwi 0",
                    "scenes": [
                        {"id": 1, "text": "Pierwsza scena", "prompt": "Door", "motion": "push_in"},
                        {"id": 2, "text": "Druga scena", "prompt": "Detail", "motion": "static"},
                    ],
                }
            )

            first = images / "scene-01.png"
            Image.new("RGB", (90, 160), "black").save(first)
            with StudioStore(db) as store:
                store.upsert_project(project)
                AssetManager(store).register_asset("001", 1, first, source="gpt-browser-manual")

            old_output = os.environ.get("CSP_OUTPUT_DIR")
            old_db = os.environ.get("CSP_STUDIO_DB")
            os.environ["CSP_OUTPUT_DIR"] = str(output)
            os.environ["CSP_STUDIO_DB"] = str(db)
            try:
                import csp_studio.web_app as web_app

                web_app = importlib.reload(web_app)
                client = TestClient(web_app.app)

                projects = client.get("/api/projects")
                self.assertEqual(projects.status_code, 200)
                self.assertEqual(projects.json()[0]["project_id"], "001")

                scenes = client.get("/api/projects/001/scenes")
                self.assertEqual(scenes.status_code, 200)
                scene = scenes.json()[0]
                self.assertEqual(scene["scene_id"], 1)
                self.assertEqual(scene["active_asset"]["revision"], 1)
                initial_revision = scene["scene_revision"]

                audit = client.get("/api/projects/001/shot-audit")
                self.assertEqual(audit.status_code, 200)
                self.assertIn("score", audit.json())
                self.assertIn("warnings", audit.json())

                edit = client.put(
                    "/api/projects/001/scenes/1",
                    json={
                        "prompt": "Updated documentary basement prompt",
                        "motion": "static",
                        "shot": {
                            "shot_type": "detail",
                            "camera": "static",
                            "purpose": "evidence",
                            "visual_anchor": "basement_door",
                            "motion_intensity": "none",
                        },
                        "note": "web editor test",
                    },
                )
                self.assertEqual(edit.status_code, 200, edit.text)
                self.assertTrue(edit.json()["changed"])
                edited_scene = edit.json()["scene"]
                self.assertEqual(edited_scene["prompt"], "Updated documentary basement prompt")
                self.assertEqual(edited_scene["shot"]["shot_type"], "detail")
                self.assertEqual(edited_scene["shot"]["visual_anchor"], "basement_door")
                self.assertEqual(edited_scene["motion"], "static")
                self.assertEqual(edited_scene["scene_revision"], initial_revision + 1)

                no_change = client.put(
                    "/api/projects/001/scenes/1",
                    json={
                        "prompt": "Updated documentary basement prompt",
                        "motion": "static",
                        "shot": {
                            "shot_type": "detail",
                            "camera": "static",
                            "purpose": "evidence",
                            "visual_anchor": "basement_door",
                            "motion_intensity": "none",
                        },
                        "note": "same values",
                    },
                )
                self.assertEqual(no_change.status_code, 200, no_change.text)
                self.assertFalse(no_change.json()["changed"])
                self.assertEqual(no_change.json()["scene"]["scene_revision"], initial_revision + 1)

                history = client.get("/api/projects/001/scenes/1/history")
                self.assertEqual(history.status_code, 200)
                self.assertTrue(
                    any(item["action"] == "edit_scene_plan" for item in history.json()["scene_revisions"])
                )

                replacement = BytesIO()
                Image.new("RGB", (90, 160), "white").save(replacement, format="PNG")
                replacement.seek(0)
                response = client.post(
                    "/api/projects/001/scenes/1/replace",
                    files={"file": ("new.png", replacement.getvalue(), "image/png")},
                    data={"source": "gpt-browser-manual", "note": "web test"},
                )
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["asset"]["revision"], 2)
                self.assertTrue((images / "scene-01.png").is_file())
                self.assertTrue((images / "revisions" / "scene-01-r1.png").is_file())
                self.assertTrue((images / "revisions" / "scene-01-r2.png").is_file())
            finally:
                if old_output is None:
                    os.environ.pop("CSP_OUTPUT_DIR", None)
                else:
                    os.environ["CSP_OUTPUT_DIR"] = old_output
                if old_db is None:
                    os.environ.pop("CSP_STUDIO_DB", None)
                else:
                    os.environ["CSP_STUDIO_DB"] = old_db


if __name__ == "__main__":
    unittest.main()
