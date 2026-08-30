from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import yaml

from csp_studio.import_short import project_from_short
from csp_studio.store import StudioStore
from csp_studio.task_runner import SUPPORTED_STAGES, StudioTaskRunner


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


if __name__ == "__main__":
    unittest.main()
