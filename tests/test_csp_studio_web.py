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
    def test_gui_edits_scene_plan_replaces_image_and_reports_ops_dashboard(self) -> None:
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
                import csp_studio.ops_api as ops_api
                import csp_studio.web_app as web_app

                importlib.reload(ops_api)
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

                ops = client.get("/api/projects/001/ops-dashboard")
                self.assertEqual(ops.status_code, 200, ops.text)
                ops_data = ops.json()
                self.assertEqual(ops_data["agent"]["next_action"], "fix_scene_plan")
                self.assertEqual(ops_data["review"]["total"], 2)
                self.assertEqual(ops_data["review"]["approved"], 0)
                self.assertIn("visual_qa", ops_data)
                self.assertIn("memory", ops_data)
                self.assertEqual(len(ops_data["pipeline"]), 6)
                initial_visual = next(item for item in ops_data["pipeline"] if item["action"] == "visual_qa")
                self.assertEqual(initial_visual["state"], "blocked")
                self.assertEqual(initial_visual["freshness"], "not_ready")
                self.assertEqual(ops_data["tasks"], [])

                enqueue = client.post("/api/projects/001/agent/enqueue-next")
                self.assertEqual(enqueue.status_code, 200, enqueue.text)
                self.assertFalse(enqueue.json()["queued"])
                self.assertEqual(enqueue.json()["reason"], "fix_scene_plan")

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

                after_replace = client.get("/api/projects/001/ops-dashboard")
                replaced_visual = next(
                    item for item in after_replace.json()["pipeline"] if item["action"] == "visual_qa"
                )
                self.assertEqual(replaced_visual["freshness"], "stale")
                self.assertIn("scene 1 image revision changed", replaced_visual["freshness_reason"])

                approve = client.post(
                    "/api/projects/001/scenes/1/approve",
                    data={"note": "ops dashboard review test"},
                )
                self.assertEqual(approve.status_code, 200, approve.text)
                self.assertEqual(approve.json()["status"], "approved")

                after_review = client.get("/api/projects/001/ops-dashboard")
                self.assertEqual(after_review.status_code, 200, after_review.text)
                self.assertEqual(after_review.json()["review"]["approved"], 1)
                self.assertEqual(after_review.json()["review"]["pending_ids"], [2])

                regenerate = client.post(
                    "/api/projects/001/scenes/1/regenerate",
                    data={"note": "regenerate button test"},
                )
                self.assertEqual(regenerate.status_code, 200, regenerate.text)
                regen_data = regenerate.json()
                self.assertTrue(regen_data["queued"])
                self.assertEqual(regen_data["scene"]["status"], "needs_regeneration")
                self.assertEqual(regen_data["task"]["stage"], "regenerate_image")
                self.assertEqual(regen_data["task"]["scene_id"], 1)
                self.assertEqual(regen_data["task"]["state"], "queued")

                dashboard_with_task = client.get("/api/projects/001/ops-dashboard")
                self.assertEqual(dashboard_with_task.status_code, 200)
                self.assertEqual(dashboard_with_task.json()["tasks"][0]["stage"], "regenerate_image")

                task_id = regen_data["task"]["task_id"]
                pending_log = client.get(f"/api/tasks/{task_id}/log")
                self.assertEqual(pending_log.status_code, 200, pending_log.text)
                self.assertFalse(pending_log.json()["available"])

                task_logs = output / ".studio-tasks"
                task_logs.mkdir()
                (task_logs / f"{task_id}.log").write_text(
                    "NVIDIA_API_KEY=do-not-return\n"
                    "Authorization: Bearer secret-token\n"
                    '{"NVIDIA_API_KEY":"json-secret"}\n'
                    "tool --api-key cli-secret\n"
                    "normal task output\n",
                    encoding="utf-8",
                )
                task_log = client.get(f"/api/tasks/{task_id}/log")
                self.assertEqual(task_log.status_code, 200, task_log.text)
                log_data = task_log.json()
                self.assertTrue(log_data["available"])
                self.assertEqual(log_data["stage"], "regenerate_image")
                self.assertIn("normal task output", log_data["content"])
                self.assertNotIn("do-not-return", log_data["content"])
                self.assertNotIn("secret-token", log_data["content"])
                self.assertNotIn("json-secret", log_data["content"])
                self.assertNotIn("cli-secret", log_data["content"])
                self.assertGreaterEqual(log_data["content"].count("[REDACTED]"), 4)

                (task_logs / f"{task_id}.log").write_text(
                    "NVIDIA_API_KEY=" + ("s" * 70000),
                    encoding="utf-8",
                )
                truncated_log = client.get(f"/api/tasks/{task_id}/log")
                self.assertEqual(truncated_log.status_code, 200, truncated_log.text)
                truncated_data = truncated_log.json()
                self.assertTrue(truncated_data["truncated"])
                self.assertEqual(truncated_data["content"], "")
                self.assertLessEqual(len(truncated_data["content"].encode("utf-8")), 64 * 1024)

                missing_log = client.get("/api/tasks/not-a-real-task/log")
                self.assertEqual(missing_log.status_code, 404)
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
