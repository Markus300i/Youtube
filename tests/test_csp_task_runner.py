from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from csp_studio.import_short import project_from_short
from csp_studio.store import StudioStore
from csp_studio.task_runner import SUPPORTED_STAGES, StudioTaskRunner, run_task_waiting


class StudioTaskRunnerTests(unittest.TestCase):
    def test_snapshot_uses_latest_sqlite_scene_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "output"
            output.mkdir()
            source = root / "001.yaml"
            payload = {
                "id": "001",
                "title": "Drzwi 0",
                "image_model": "z-image-turbo",
                "scenes": [
                    {"id": index, "text": f"Text {index}", "prompt": f"OLD {index}", "motion": "static"}
                    for index in range(1, 9)
                ],
            }
            source.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
            db = output / "studio.db"
            with StudioStore(db) as store:
                project = project_from_short(payload, str(source))
                store.upsert_project(project)
                scene = store.get_scene("001", 4)
                scene.prompt = "NEW PROMPT FROM STUDIO"
                scene.motion = "slow_push"
                store.upsert_scene(scene, action="edit_scene_plan", note="test")

                runner = StudioTaskRunner(db, output_root=output, python_executable="python")
                snapshot = runner._write_snapshot(store, "001")

            saved = yaml.safe_load(snapshot.read_text(encoding="utf-8"))
            scene4 = next(item for item in saved["scenes"] if int(item["id"]) == 4)
            self.assertEqual(scene4["prompt"], "NEW PROMPT FROM STUDIO")
            self.assertEqual(scene4["motion"], "slow_push")
            self.assertEqual(len(saved["scenes"]), 8)

    def test_runner_only_allows_known_pipeline_stages(self) -> None:
        self.assertEqual(
            SUPPORTED_STAGES,
            {
                "regenerate_image",
                "tts",
                "captions",
                "sound_design",
                "visual_qa",
                "opencut_export",
                "render_final",
            },
        )

    def test_stage_validation_rejects_missing_tts_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "output"
            output.mkdir()
            db = output / "studio.db"
            payload = {
                "id": "001",
                "title": "Drzwi 0",
                "scenes": [{"id": 1, "text": "A", "prompt": "A"}],
            }
            with StudioStore(db) as store:
                store.upsert_project(project_from_short(payload))
                runner = StudioTaskRunner(db, output_root=output, python_executable="python")
                with self.assertRaises(FileNotFoundError):
                    runner._validate_stage_artifacts(store, "001", "tts")

                audio = output / "001-drzwi-0" / "audio"
                audio.mkdir(parents=True)
                (audio / "voice.wav").write_bytes(b"voice")
                with self.assertRaises(FileNotFoundError):
                    runner._validate_stage_artifacts(store, "001", "tts")

                (audio / "tts-timings.json").write_text("{}", encoding="utf-8")
                primary, artifacts = runner._validate_stage_artifacts(store, "001", "tts")
                self.assertEqual(primary.name, "voice.wav")
                self.assertEqual({path.name for path in artifacts}, {"voice.wav", "tts-timings.json"})

    def test_waiting_worker_retries_queued_task_until_claimed(self) -> None:
        with patch(
            "csp_studio.task_runner.run_task",
            side_effect=[
                {"task_id": "t1", "state": "queued"},
                {"task_id": "t1", "state": "queued"},
                {"task_id": "t1", "state": "succeeded", "progress": 100},
            ],
        ) as mocked, patch("csp_studio.task_runner.time.sleep"):
            result = run_task_waiting(
                "t1",
                db_path="unused.db",
                output_root="unused-output",
                poll_seconds=0.2,
                max_wait_seconds=30,
            )
        self.assertEqual(result["state"], "succeeded")
        self.assertEqual(mocked.call_count, 3)


if __name__ == "__main__":
    unittest.main()
