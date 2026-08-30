from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from csp_studio.import_short import project_from_short
from csp_studio.store import StudioStore
from csp_studio.studio_worker import StudioWorker
from csp_studio.task_engine import TaskEngine
from csp_studio.worker_registry import WorkerRegistry


class StudioWorkerTests(unittest.TestCase):
    def test_recover_abandoned_running_task_requeues_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "output"
            output.mkdir()
            db = output / "studio.db"
            with StudioStore(db) as store:
                store.upsert_project(project_from_short({"id": "001", "title": "Drzwi 0", "scenes": []}))
                engine = TaskEngine(store)
                task = engine.submit("001", "tts", resource="gpu")
                claimed = engine.claim(task.task_id, "dead-worker")
                self.assertIsNotNone(claimed)
                store.conn.execute(
                    "UPDATE studio_tasks SET updated_at=? WHERE task_id=?",
                    ("2000-01-01T00:00:00+00:00", task.task_id),
                )
                store.conn.commit()

            worker = StudioWorker(db, output_root=output, lease_seconds=10, heartbeat_seconds=1)
            recovered = worker.recover_abandoned()
            self.assertEqual(recovered, [task.task_id])
            with StudioStore(db) as store:
                current = TaskEngine(store).get(task.task_id)
                self.assertEqual(current.state, "queued")
                self.assertIsNone(current.worker_id)
                self.assertEqual(current.failed_stage, "worker_recovery")

    def test_idle_run_once_registers_online_worker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "output"
            output.mkdir()
            db = output / "studio.db"
            with StudioStore(db) as store:
                store.upsert_project(project_from_short({"id": "001", "title": "Idle", "scenes": []}))

            worker = StudioWorker(
                db,
                output_root=output,
                worker_id="test-idle-worker",
                heartbeat_seconds=1,
                lease_seconds=10,
            )
            self.assertIsNone(worker.run_once())

            with StudioStore(db) as store:
                status = WorkerRegistry(store).get("test-idle-worker", online_ttl_seconds=20)
                self.assertIsNotNone(status)
                assert status is not None
                self.assertTrue(status.online)
                self.assertEqual(status.state, "idle")
                self.assertIsNone(status.current_task_id)
                self.assertEqual(Path(status.metadata["db_path"]), db.resolve())


if __name__ == "__main__":
    unittest.main()
