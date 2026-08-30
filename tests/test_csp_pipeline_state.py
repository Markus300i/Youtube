from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from csp_studio.import_short import project_from_short
from csp_studio.pipeline_state import (
    invalidate_after_image_change,
    invalidate_after_stage,
    is_stale,
    mark_done,
)
from csp_studio.store import StudioStore
from csp_studio.task_engine import TaskEngine


class PipelineFreshnessTests(unittest.TestCase):
    def _project(self):
        return project_from_short(
            {
                "id": "001",
                "title": "Drzwi 0",
                "scenes": [{"id": 1, "text": "A", "prompt": "A"}],
            }
        )

    def test_image_change_invalidates_visual_opencut_and_final(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with StudioStore(Path(tmp) / "studio.db") as store:
                store.upsert_project(self._project())
                engine = TaskEngine(store)
                invalidate_after_image_change(
                    engine,
                    "001",
                    scene_id=1,
                    reason="image changed",
                )
                self.assertTrue(is_stale(engine, "001", "visual_qa"))
                self.assertTrue(is_stale(engine, "001", "opencut_export"))
                self.assertTrue(is_stale(engine, "001", "render_final"))
                self.assertEqual(
                    engine.get_checkpoint("001", "visual_qa_scene_01")["state"],
                    "stale",
                )

    def test_tts_change_invalidates_audio_dependents(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with StudioStore(Path(tmp) / "studio.db") as store:
                store.upsert_project(self._project())
                engine = TaskEngine(store)
                invalidate_after_stage(engine, "001", "tts", reason="new voice")
                for stage in ("captions", "sound_design", "opencut_export", "render_final"):
                    self.assertTrue(is_stale(engine, "001", stage), stage)

    def test_legacy_visual_stale_is_persisted_to_downstream(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with StudioStore(Path(tmp) / "studio.db") as store:
                store.upsert_project(self._project())
                engine = TaskEngine(store)
                engine.set_checkpoint(
                    "001",
                    "visual_qa",
                    "stale",
                    metadata={"reason": "old Studio image regeneration"},
                )
                self.assertIsNone(engine.get_checkpoint("001", "render_final"))
                self.assertTrue(is_stale(engine, "001", "render_final"))
                self.assertEqual(engine.get_checkpoint("001", "render_final")["state"], "stale")
                self.assertEqual(engine.get_checkpoint("001", "opencut_export")["state"], "stale")

    def test_newer_opencut_success_is_not_restaled_by_legacy_reconciliation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "csp-opencut.json"
            manifest.write_text("{}", encoding="utf-8")
            with StudioStore(Path(tmp) / "studio.db") as store:
                store.upsert_project(self._project())
                engine = TaskEngine(store)
                engine.set_checkpoint(
                    "001",
                    "visual_qa",
                    "stale",
                    metadata={"reason": "image changed"},
                )
                store.conn.execute(
                    "UPDATE pipeline_checkpoints SET updated_at=? WHERE project_id=? AND stage=?",
                    ("2000-01-01T00:00:00+00:00", "001", "visual_qa"),
                )
                store.conn.commit()
                mark_done(engine, "001", "opencut_export", artifact_path=manifest)

                self.assertFalse(is_stale(engine, "001", "opencut_export"))
                self.assertEqual(engine.get_checkpoint("001", "opencut_export")["state"], "done")


if __name__ == "__main__":
    unittest.main()
