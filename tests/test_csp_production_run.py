from __future__ import annotations

import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

from csp_studio.import_short import project_from_short
from csp_studio.production_run import ProductionRunCoordinator
from csp_studio.store import StudioStore
from csp_studio.task_engine import TaskEngine


@dataclass
class FakeReport:
    next_action: str

    def to_dict(self):
        return {"next_action": self.next_action}


class ProductionRunTests(unittest.TestCase):
    def _project(self):
        return project_from_short(
            {
                "id": "001",
                "title": "Drzwi 0",
                "fictional": True,
                "scenes": [
                    {"id": i, "text": f"Scene {i}", "prompt": f"Prompt {i}"}
                    for i in range(1, 9)
                ],
            }
        )

    def test_start_stops_at_missing_images_human_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "studio.db"
            with StudioStore(db) as store:
                store.upsert_project(self._project())
                result = ProductionRunCoordinator(store, output_root=Path(tmp) / "output").start("001")
            self.assertFalse(result["advanced"])
            self.assertEqual(result["reason"], "complete_images")
            self.assertEqual(result["run"]["state"], "blocked")
            self.assertTrue(result["run"]["enabled"])

    def test_advance_schedules_agent_one_stage_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "studio.db"
            with StudioStore(db) as store:
                store.upsert_project(self._project())
                coordinator = ProductionRunCoordinator(store, output_root=Path(tmp) / "output")
                coordinator._save("001", enabled=True, state="running")
                coordinator.agent.inspect = lambda project_id: FakeReport("generate_tts")  # type: ignore[method-assign]
                first = coordinator.advance("001")
                second = coordinator.advance("001")
                tasks = TaskEngine(store).list("001")
            self.assertTrue(first["advanced"])
            self.assertEqual(first["task"]["stage"], "tts")
            self.assertFalse(second["advanced"])
            self.assertEqual(second["reason"], "already_active")
            self.assertEqual(len(tasks), 1)


if __name__ == "__main__":
    unittest.main()
