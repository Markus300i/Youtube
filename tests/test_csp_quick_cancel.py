from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from csp_studio.action_api import _run_logged
from csp_studio.import_short import project_from_short
from csp_studio.store import StudioStore
from csp_studio.task_engine import TaskEngine


class QuickCancelTests(unittest.TestCase):
    def test_run_logged_terminates_process_after_task_cancel(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "studio.db"
            log = root / "quick.log"
            with StudioStore(db) as store:
                store.upsert_project(project_from_short({"id": "001", "title": "Drzwi 0", "scenes": []}))
                task = TaskEngine(store).submit("001", "regenerate_image_quick", resource="gpu")
                TaskEngine(store).claim(task.task_id, "studio-web-quick")

            def cancel() -> None:
                with StudioStore(db) as store:
                    TaskEngine(store).cancel(task.task_id)

            timer = threading.Timer(0.3, cancel)
            timer.start()
            started = time.monotonic()
            try:
                rc = _run_logged(
                    [sys.executable, "-c", "import time; print('quick-start'); time.sleep(30)"],
                    log,
                    os.environ.copy(),
                    task_id=task.task_id,
                    db_path=db,
                )
            finally:
                timer.cancel()

            self.assertNotEqual(rc, 0)
            self.assertLess(time.monotonic() - started, 6)
            with StudioStore(db) as store:
                self.assertEqual(TaskEngine(store).get(task.task_id).state, "cancelled")


if __name__ == "__main__":
    unittest.main()
