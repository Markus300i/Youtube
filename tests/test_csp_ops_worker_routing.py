from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from csp_studio import ops_api
from csp_studio.import_short import project_from_short
from csp_studio.store import StudioStore
from csp_studio.task_engine import TaskEngine


class OpsWorkerRoutingTests(unittest.TestCase):
    def _seed(self, db: Path) -> None:
        with StudioStore(db) as store:
            store.upsert_project(
                project_from_short(
                    {
                        "id": "ops-worker",
                        "title": "Ops Worker",
                        "fictional": True,
                        "scenes": [{"id": 1, "text": "Scene text", "prompt": "Scene prompt"}],
                    }
                )
            )

    def test_run_endpoint_keeps_task_queued_for_worker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "studio.db"
            self._seed(db)
            with StudioStore(db) as store:
                task = TaskEngine(store).submit("ops-worker", "tts", resource="gpu")

            app = FastAPI()
            app.include_router(ops_api.router)
            client = TestClient(app)
            with patch.object(ops_api, "DB_PATH", db):
                response = client.post(f"/api/tasks/{task.task_id}/run")

            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["execution"], "studio_worker")
            with StudioStore(db) as store:
                queued = TaskEngine(store).get(task.task_id)
                self.assertIsNotNone(queued)
                assert queued is not None
                self.assertEqual(queued.state, "queued")
                self.assertIsNone(queued.worker_id)

    def test_retry_endpoint_requeues_for_worker_without_executing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "studio.db"
            self._seed(db)
            with StudioStore(db) as store:
                engine = TaskEngine(store)
                task = engine.submit("ops-worker", "tts", resource="gpu")
                engine.claim(task.task_id, "old-worker")
                engine.fail(task.task_id, "test failure")

            app = FastAPI()
            app.include_router(ops_api.router)
            client = TestClient(app)
            with patch.object(ops_api, "DB_PATH", db):
                response = client.post(f"/api/tasks/{task.task_id}/retry")

            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["execution"], "studio_worker")
            with StudioStore(db) as store:
                queued = TaskEngine(store).get(task.task_id)
                self.assertIsNotNone(queued)
                assert queued is not None
                self.assertEqual(queued.state, "queued")
                self.assertIsNone(queued.worker_id)


if __name__ == "__main__":
    unittest.main()
