from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from csp_studio.import_short import project_from_short
from csp_studio.store import StudioStore
from csp_studio.task_engine import TaskEngine
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
                "regenerate_image_quick",
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

    def test_failure_does_not_persist_raw_log_tail_or_secret(self) -> None:
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
                task = TaskEngine(store).submit("001", "tts", resource="gpu")

            runner = StudioTaskRunner(db, output_root=output, python_executable="python")

            def fail_with_secret(task_id: str, log_path: Path) -> dict:
                log_path.parent.mkdir(parents=True, exist_ok=True)
                log_path.write_text("NVIDIA_API_KEY=log-secret", encoding="utf-8")
                raise RuntimeError("TOKEN=exception-secret")

            with patch.object(runner, "_execute", side_effect=fail_with_secret):
                result = runner.run(task.task_id)

            self.assertEqual(result["state"], "failed")
            self.assertEqual(result["error"], "RuntimeError: TOKEN=[REDACTED]")
            self.assertNotIn("log-secret", result["error"])
            self.assertNotIn("exception-secret", result["error"])
            self.assertNotIn("| log:", result["error"])

    def test_display_command_redacts_cli_secret(self) -> None:
        command = StudioTaskRunner._display_command(["tool", "--api-key", "cli-secret"])
        self.assertNotIn("cli-secret", command)
        self.assertIn("[REDACTED]", command)

    def test_process_output_is_redacted_before_log_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "output"
            output.mkdir()
            db = output / "studio.db"
            with StudioStore(db) as store:
                store.upsert_project(project_from_short({"id": "001", "title": "Drzwi 0", "scenes": []}))
                task = TaskEngine(store).submit("001", "tts", resource="gpu")
                TaskEngine(store).claim(task.task_id, "test-worker")

            log_path = output / ".studio-tasks" / f"{task.task_id}.log"
            runner = StudioTaskRunner(db, output_root=output, python_executable=sys.executable)
            returncode = runner._run_process(
                task.task_id,
                [sys.executable, "-c", "print('NVIDIA_API_KEY=stdout-secret')"],
                log_path,
                env=os.environ.copy(),
            )

            self.assertEqual(returncode, 0)
            content = log_path.read_text(encoding="utf-8")
            self.assertNotIn("stdout-secret", content)
            self.assertIn("NVIDIA_API_KEY=[REDACTED]", content)

    def test_process_output_pump_preserves_task_cancellation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "output"
            output.mkdir()
            db = output / "studio.db"
            with StudioStore(db) as store:
                store.upsert_project(project_from_short({"id": "001", "title": "Drzwi 0", "scenes": []}))
                task = TaskEngine(store).submit("001", "tts", resource="gpu")
                TaskEngine(store).claim(task.task_id, "test-worker")

            def cancel_task() -> None:
                with StudioStore(db) as store:
                    TaskEngine(store).cancel(task.task_id)

            timer = threading.Timer(0.2, cancel_task)
            runner = StudioTaskRunner(db, output_root=output, python_executable=sys.executable)
            started = time.monotonic()
            timer.start()
            try:
                returncode = runner._run_process(
                    task.task_id,
                    [sys.executable, "-c", "import time; print('started'); time.sleep(10)"],
                    output / ".studio-tasks" / f"{task.task_id}.log",
                    env=os.environ.copy(),
                )
            finally:
                timer.cancel()

            self.assertNotEqual(returncode, 0)
            self.assertLess(time.monotonic() - started, 5)


if __name__ == "__main__":
    unittest.main()