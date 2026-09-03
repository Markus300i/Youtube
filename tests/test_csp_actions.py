from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import yaml

from csp_studio.action_api import ACTION_META, MANUAL_ACTIONS, _quick_snapshot, _run_logged, action_statuses
from csp_studio.import_short import project_from_short
from csp_studio.store import StudioStore
from csp_studio.task_engine import TaskEngine


class StudioActionsTests(unittest.TestCase):
    def test_quick_snapshot_forces_zimage_and_skips_flux_for_target_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "current.yaml"
            target = root / "quick.yaml"
            source.write_text(
                yaml.safe_dump(
                    {
                        "id": "001",
                        "title": "Drzwi 0",
                        "image_model": "flux2-klein",
                        "continuity": {"global": "keep same basement"},
                        "scenes": [
                            {
                                "id": 1,
                                "prompt": "master door",
                                "render": {"mode": "generate"},
                            },
                            {
                                "id": 4,
                                "prompt": "tenant listening",
                                "continuity_refs": ["basement", "basement_door", "tenant"],
                                "render": {
                                    "mode": "flux_edit",
                                    "reference_scene": 1,
                                    "edit_rects": [[0.03, 0.30, 0.49, 0.96]],
                                },
                            },
                        ],
                    },
                    allow_unicode=True,
                    sort_keys=False,
                ),
                encoding="utf-8",
            )

            _quick_snapshot(source, 4, target)
            data = yaml.safe_load(target.read_text(encoding="utf-8"))
            self.assertEqual(data["image_model"], "z-image-turbo")
            scene1 = next(scene for scene in data["scenes"] if scene["id"] == 1)
            scene4 = next(scene for scene in data["scenes"] if scene["id"] == 4)
            self.assertEqual(scene1["render"], {"mode": "generate"})
            self.assertEqual(scene4["render"], {"mode": "generate"})
            self.assertEqual(scene4["prompt"], "tenant listening")
            self.assertEqual(scene4["continuity_refs"], ["basement", "basement_door", "tenant"])
            self.assertEqual(data["continuity"]["global"], "keep same basement")

    def test_manual_action_map_uses_existing_pipeline_stages(self) -> None:
        self.assertEqual(MANUAL_ACTIONS["tts"], ("tts", "gpu"))
        self.assertEqual(MANUAL_ACTIONS["captions"], ("captions", "gpu"))
        self.assertEqual(MANUAL_ACTIONS["sound_design"], ("sound_design", "cpu"))
        self.assertEqual(MANUAL_ACTIONS["visual_qa"], ("visual_qa", "network"))
        self.assertEqual(MANUAL_ACTIONS["opencut_export"], ("opencut_export", "io"))
        self.assertEqual(MANUAL_ACTIONS["render_final"], ("render_final", "gpu"))

    def test_manual_action_dependencies_protect_pipeline_order(self) -> None:
        self.assertEqual(ACTION_META["tts"]["requires"], ())
        self.assertEqual(ACTION_META["captions"]["requires"], ("tts",))
        self.assertEqual(ACTION_META["sound_design"]["requires"], ("tts",))
        self.assertEqual(ACTION_META["visual_qa"]["requires"], ("active_images",))
        self.assertEqual(
            ACTION_META["opencut_export"]["requires"],
            ("active_images", "tts", "captions", "sound_design"),
        )
        self.assertEqual(
            ACTION_META["render_final"]["requires"],
            ("active_images", "tts", "captions", "sound_design", "scene_review"),
        )

    def test_quick_runner_command_log_redacts_cli_secret(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "quick.log"
            result = _run_logged(
                [
                    sys.executable,
                    "-c",
                    "print('TOKEN=process-secret')",
                    "--token",
                    "quick-secret",
                ],
                log_path,
                os.environ.copy(),
            )

            self.assertEqual(result, 0)
            content = log_path.read_text(encoding="utf-8")
            self.assertNotIn("quick-secret", content)
            self.assertNotIn("process-secret", content)
            self.assertGreaterEqual(content.count("[REDACTED]"), 2)

    def test_action_status_reports_resource_wait_and_running_step(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "studio.db"
            with StudioStore(db) as store:
                for project_id in ("001", "002"):
                    store.upsert_project(
                        project_from_short(
                            {
                                "id": project_id,
                                "title": f"Project {project_id}",
                                "scenes": [{"id": 1, "text": "A", "prompt": "A"}],
                            }
                        )
                    )
                engine = TaskEngine(store)
                blocker = engine.submit("002", "regenerate_image", resource="gpu")
                engine.claim(blocker.task_id, "gpu-worker")
                queued = engine.submit("001", "tts", resource="gpu")
                running = engine.submit("001", "visual_qa", resource="network")
                engine.claim(running.task_id, "network-worker")
                engine.progress(running.task_id, 35, stage="scene_03")

                statuses = {
                    item["action"]: item
                    for item in action_statuses(
                        store,
                        "001",
                        report=SimpleNamespace(checks=[]),
                    )
                }

            self.assertEqual(statuses["tts"]["state"], "queued")
            self.assertEqual(statuses["tts"]["active_progress"], queued.progress)
            self.assertIn(blocker.task_id, statuses["tts"]["waiting_reason"])
            self.assertEqual(statuses["tts"]["blocking_task"]["task_id"], blocker.task_id)
            self.assertEqual(statuses["visual_qa"]["state"], "running")
            self.assertEqual(statuses["visual_qa"]["active_progress"], 35)
            self.assertEqual(statuses["visual_qa"]["current_step"], "scene_03")


if __name__ == "__main__":
    unittest.main()
