from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from csp_studio.asset_manager import AssetManager
from csp_studio.import_short import project_from_short
from csp_studio.providers.base import ProviderResponse
from csp_studio.store import StudioStore
from csp_studio.visual_qa import VisualQA


class FakeVisionProvider:
    name = "fake_vision"

    def __init__(self, text: str):
        self.text = text
        self.prompt = None
        self.image_paths = None

    def analyze_images(self, prompt, image_paths, *, model=None, temperature=0.1, max_tokens=1600):
        self.prompt = prompt
        self.image_paths = list(image_paths)
        return ProviderResponse(provider=self.name, model="fake-vlm", text=self.text)


class VisualQATests(unittest.TestCase):
    def _project(self):
        return project_from_short(
            {
                "id": "001",
                "title": "Drzwi 0",
                "visual_style": "Polish documentary thriller",
                "scenes": [
                    {
                        "id": index,
                        "text": f"Narracja sceny {index}",
                        "prompt": f"Prompt {index}",
                        "motion": "static" if index == 8 else "slow_push",
                    }
                    for index in range(1, 9)
                ],
            }
        )

    def _attach_images(self, store: StudioStore, project_dir: Path):
        manager = AssetManager(store)
        images = project_dir / "images"
        images.mkdir(parents=True, exist_ok=True)
        for index in range(1, 9):
            path = images / f"scene-{index:02d}.png"
            Image.new("RGB", (1080, 1920), (index * 20, index * 20, index * 20)).save(path)
            manager.register_asset("001", index, path, source="gpt-browser-manual")

    def test_visual_qa_sends_eight_thumbnails_and_persists_report_checkpoint(self):
        payload = {
            "score": 74,
            "summary": "Good continuity but the middle repeats the same frontal framing.",
            "warnings": ["Scenes 3-5 are too similar."],
            "continuity": ["Door orientation is stable."],
            "monotony": ["Three consecutive frontal door shots."],
            "scene_notes": [
                {
                    "scene_id": 4,
                    "severity": "warning",
                    "issue": "Framing repeats Scene 3.",
                    "recommendation": "Use POV or detail framing.",
                }
            ],
        }
        provider = FakeVisionProvider(json.dumps(payload))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_dir = root / "001-drzwi-0"
            with StudioStore(root / "studio.db") as store:
                store.upsert_project(self._project())
                self._attach_images(store, project_dir)
                qa = VisualQA(store, output_root=root)
                report, report_path = qa.run("001", provider)

                self.assertEqual(report.score, 74)
                self.assertEqual(report.provider, "fake_vision")
                self.assertEqual(report.model, "fake-vlm")
                self.assertEqual(report.scene_notes[0].scene_id, 4)
                self.assertEqual(len(provider.image_paths), 8)
                self.assertIn("IN THAT ORDER", provider.prompt)
                self.assertIn("Narracja sceny 4", provider.prompt)

                for path_str in provider.image_paths:
                    path = Path(path_str)
                    self.assertTrue(path.is_file())
                    self.assertEqual(path.suffix.lower(), ".jpg")
                    with Image.open(path) as image:
                        self.assertLessEqual(image.width, 432)
                        self.assertLessEqual(image.height, 768)

                self.assertTrue(report_path.is_file())
                saved = json.loads(report_path.read_text(encoding="utf-8"))
                self.assertEqual(saved["score"], 74)
                checkpoint = qa.tasks.get_checkpoint("001", "visual_qa")
                self.assertEqual(checkpoint["state"], "done")
                self.assertEqual(checkpoint["metadata"]["score"], 74)
                self.assertTrue(qa.tasks.checkpoint_is_usable("001", "visual_qa"))

    def test_invalid_provider_json_marks_checkpoint_failed(self):
        provider = FakeVisionProvider("not json at all")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_dir = root / "001-drzwi-0"
            with StudioStore(root / "studio.db") as store:
                store.upsert_project(self._project())
                self._attach_images(store, project_dir)
                qa = VisualQA(store, output_root=root)
                with self.assertRaises(Exception):
                    qa.run("001", provider)
                checkpoint = qa.tasks.get_checkpoint("001", "visual_qa")
                self.assertEqual(checkpoint["state"], "failed")
                self.assertIn("error", checkpoint["metadata"])


if __name__ == "__main__":
    unittest.main()
