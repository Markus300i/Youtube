from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from csp_studio import action_api
from csp_studio.action_api import router
from csp_studio.import_short import project_from_short
from csp_studio.store import StudioStore
from csp_studio.studio_worker import StudioWorker
from csp_studio.task_engine import TaskEngine
from csp_studio.task_runner import SUPPORTED_STAGES, StudioTaskRunner


def _seed_project(db: Path, project_id: str = "worker-plane") -> None:
    with StudioStore(db) as store:
        store.upsert_project(
            project_from_short(
                {
                    "id": project_id,
                    "title": "Worker Plane",
                    "fictional": True,
                    "scenes": [{"id": 1, "text": "Test scene text", "prompt": "Test scene prompt"}],
                }
            )
        )


class WorkerExecutionPlaneTests(unittest.TestCase):
    def test_quick_stage_is_worker_allowlisted_and_dispatched_as_quick(self) -> None:
        self.assertIn("regenerate_image_quick", SUPPORTED_STAGES)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "studio.db"
            output = root / "output"
            _seed_project(db)
            with StudioStore(db) as store:
                task = TaskEngine(store).submit(
                    "worker-plane",
                    "regenerate_image_quick",
                    scene_id=1,
                    resource="gpu",
                )

            runner = StudioTaskRunner(db, output_root=output)
            snapshot = root / "snapshot.yaml"
            with patch.object(runner, "_write_snapshot", return_value=snapshot), patch.object(
                runner,
                "_regenerate_scene",
                return_value={"mode": "quick", "model": "z-image-turbo"},
            ) as regenerate:
                result = runner.run(task.task_id, worker_id="worker-test")

            self.assertEqual(result["state"], "succeeded")
            regenerate.assert_called_once()
            args, kwargs = regenerate.call_args
            self.assertEqual(args[0], task.task_id)
            self.assertEqual(args[1], "worker-plane")
            self.assertEqual(args[2], 1)
            self.assertEqual(args[3], snapshot)
            self.assertTrue(kwargs["quick"])

    def test_worker_candidate_selects_queued_quick_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "studio.db"
            output = root / "output"
            _seed_project(db)
            with StudioStore(db) as store:
                task = TaskEngine(store).submit(
                    "worker-plane",
                    "regenerate_image_quick",
                    scene_id=1,
                    resource="gpu",
                )
            worker = StudioWorker(db, output_root=output, worker_id="worker-test")
            self.assertEqual(worker._next_candidate(), task.task_id)

    def test_quick_api_only_queues_task_for_worker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "studio.db"
            _seed_project(db)
            app = FastAPI()
            app.include_router(router)
            client = TestClient(app)

            with patch.object(action_api, "DB_PATH", db):
                response = client.post("/api/projects/worker-plane/scenes/1/quick-regenerate")

            self.assertEqual(response.status_code, 200, response.text)
            payload = response.json()
            self.assertTrue(payload["scheduled"])
            self.assertEqual(payload["execution"], "studio_worker")
            with StudioStore(db) as store:
                task = TaskEngine(store).get(payload["task"]["task_id"])
                self.assertIsNotNone(task)
                assert task is not None
                self.assertEqual(task.state, "queued")
                self.assertIsNone(task.worker_id)
                self.assertEqual(task.stage, "regenerate_image_quick")

    def test_manual_action_api_only_queues_task_for_worker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "studio.db"
            _seed_project(db)
            app = FastAPI()
            app.include_router(router)
            client = TestClient(app)

            with patch.object(action_api, "DB_PATH", db), patch.object(action_api, "OUTPUT_ROOT", root / "output"):
                response = client.post("/api/projects/worker-plane/actions/tts")

            self.assertEqual(response.status_code, 200, response.text)
            payload = response.json()
            self.assertTrue(payload["scheduled"])
            self.assertEqual(payload["execution"], "studio_worker")
            with StudioStore(db) as store:
                task = TaskEngine(store).get(payload["task"]["task_id"])
                self.assertIsNotNone(task)
                assert task is not None
                self.assertEqual(task.state, "queued")
                self.assertIsNone(task.worker_id)
                self.assertEqual(task.stage, "tts")


if __name__ == "__main__":
    unittest.main()
