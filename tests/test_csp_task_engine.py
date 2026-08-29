from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from csp_studio.import_short import project_from_short
from csp_studio.store import StudioStore
from csp_studio.task_engine import TaskEngine, atomic_write_json


class TaskEngineTests(unittest.TestCase):
    def _project(self):
        return project_from_short(
            {
                "id": "001",
                "title": "Drzwi 0",
                "scenes": [{"id": 1, "text": "A", "prompt": "A"}],
            }
        )

    def test_task_lifecycle_failure_retry_and_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "studio.db"
            with StudioStore(db) as store:
                store.upsert_project(self._project())
                engine = TaskEngine(store)
                task = engine.submit("001", "render_preview", resource="gpu", payload={"quality": "preview"})
                self.assertEqual(task.state, "queued")
                claimed = engine.claim_next("worker-a", resource="gpu")
                self.assertIsNotNone(claimed)
                self.assertEqual(claimed.task_id, task.task_id)
                self.assertEqual(claimed.state, "running")
                self.assertEqual(engine.progress(task.task_id, 42).progress, 42)

                failed = engine.fail(task.task_id, "encoder failed", failed_stage="ffmpeg")
                self.assertEqual(failed.state, "failed")
                self.assertEqual(failed.failed_stage, "ffmpeg")
                self.assertIn("encoder", failed.error or "")

                retried = engine.retry(task.task_id)
                self.assertEqual(retried.state, "queued")
                self.assertEqual(retried.retry_count, 1)
                claimed_again = engine.claim_next("worker-b", resource="gpu")
                self.assertEqual(claimed_again.task_id, task.task_id)
                complete = engine.complete(task.task_id, {"path": "preview.mp4"})
                self.assertEqual(complete.state, "succeeded")
                self.assertEqual(complete.progress, 100)
                self.assertEqual(complete.result["path"], "preview.mp4")

    def test_only_one_gpu_task_can_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "studio.db"
            with StudioStore(db) as store:
                store.upsert_project(self._project())
                engine = TaskEngine(store)
                first = engine.submit("001", "images", resource="gpu")
                second = engine.submit("001", "tts", resource="gpu")
                claimed = engine.claim_next("gpu-1", resource="gpu")
                self.assertEqual(claimed.task_id, first.task_id)
                self.assertIsNone(engine.claim_next("gpu-2", resource="gpu"))
                engine.complete(first.task_id)
                next_claim = engine.claim_next("gpu-2", resource="gpu")
                self.assertEqual(next_claim.task_id, second.task_id)

    def test_checkpoint_requires_existing_artifact_when_path_is_set(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "studio.db"
            artifact = root / "voice.wav"
            with StudioStore(db) as store:
                store.upsert_project(self._project())
                engine = TaskEngine(store)
                engine.set_checkpoint("001", "tts", "done", artifact_path=artifact)
                self.assertFalse(engine.checkpoint_is_usable("001", "tts"))
                artifact.write_bytes(b"voice")
                self.assertTrue(engine.checkpoint_is_usable("001", "tts"))
                checkpoint = engine.get_checkpoint("001", "tts")
                self.assertEqual(checkpoint["state"], "done")

    def test_atomic_write_json_replaces_complete_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "state" / "manifest.json"
            atomic_write_json(target, {"state": "first"})
            atomic_write_json(target, {"state": "second", "progress": 100})
            data = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(data, {"state": "second", "progress": 100})
            self.assertEqual(list(target.parent.glob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
