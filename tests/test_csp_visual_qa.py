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

    def __init__(self, *, fail_scene: int | None = None):
        self.image_calls: list[dict] = []
        self.chat_calls: list[dict] = []
        self.fail_scene = fail_scene

    def analyze_images(self, prompt, image_paths, *, model=None, temperature=0.1, max_tokens=1600):
        paths = list(image_paths)
        self.image_calls.append({"prompt": prompt, "paths": paths, "max_tokens": max_tokens})
        scene_id = int(Path(paths[0]).stem.split("-")[1])
        if self.fail_scene == scene_id:
            return ProviderResponse(provider=self.name, model="fake-vlm", text="not json")
        payload = {
            "scene_id": scene_id,
            "scene_score": 90 - scene_id,
            "visual_signature": {
                "framing": "medium" if scene_id in {3, 4} else f"framing-{scene_id}",
                "camera_angle": "eye-level" if scene_id in {3, 4} else f"angle-{scene_id}",
                "dominant_subject": f"subject-{scene_id}",
                "location": "basement",
                "recurring_elements": ["dark door"],
            },
            "warnings": [f"Scene {scene_id} warning"],
            "continuity_cues": ["dark door"],
            "issue": f"Issue scene {scene_id}" if scene_id == 4 else "",
            "recommendation": "Change framing" if scene_id == 4 else "",
            "severity": "warning" if scene_id == 4 else "info",
        }
        return ProviderResponse(provider=self.name, model="fake-vlm", text=json.dumps(payload))

    def chat(self, messages, *, model=None, temperature=0.1, max_tokens=1400):
        self.chat_calls.append({"messages": messages, "max_tokens": max_tokens})
        payload = {
            "score": 74,
            "summary": "Good continuity but scenes 3 and 4 repeat framing.",
            "warnings": ["Scene 4 repeats Scene 3."],
            "continuity": ["Door appearance remains stable."],
            "monotony": ["Scenes 3 and 4 share medium eye-level framing."],
            "scene_notes": [
                {
                    "scene_id": 4,
                    "severity": "warning",
                    "issue": "Framing repeats Scene 3.",
                    "recommendation": "Use POV or detail framing.",
                }
            ],
        }
        return ProviderResponse(provider=self.name, model="fake-text", text=json.dumps(payload))


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

    def test_visual_qa_uses_eight_single_images_then_text_aggregation(self):
        provider = FakeVisionProvider()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_dir = root / "001-drzwi-0"
            with StudioStore(root / "studio.db") as store:
                store.upsert_project(self._project())
                self._attach_images(store, project_dir)
                qa = VisualQA(store, output_root=root)
                report, report_path = qa.run("001", provider)

                self.assertEqual(report.score, 74)
                self.assertEqual(report.strategy, "single_scene_v1")
                self.assertEqual(len(provider.image_calls), 8)
                self.assertEqual(len(provider.chat_calls), 1)
                self.assertTrue(all(len(call["paths"]) == 1 for call in provider.image_calls))
                self.assertTrue(all(call["max_tokens"] == 650 for call in provider.image_calls))
                self.assertIn("Scene 4", provider.image_calls[3]["prompt"])
                self.assertIn("Narracja sceny 4", provider.image_calls[3]["prompt"])

                for call in provider.image_calls:
                    path = Path(call["paths"][0])
                    self.assertTrue(path.is_file())
                    self.assertEqual(path.suffix.lower(), ".jpg")
                    with Image.open(path) as image:
                        self.assertLessEqual(image.width, 360)
                        self.assertLessEqual(image.height, 640)

                self.assertTrue(report_path.is_file())
                saved = json.loads(report_path.read_text(encoding="utf-8"))
                self.assertEqual(saved["score"], 74)
                self.assertEqual(saved["strategy"], "single_scene_v1")
                checkpoint = qa.tasks.get_checkpoint("001", "visual_qa")
                self.assertEqual(checkpoint["state"], "done")
                self.assertEqual(checkpoint["metadata"]["scenes"], 8)
                for scene_id in range(1, 9):
                    scene_checkpoint = qa.tasks.get_checkpoint("001", f"visual_qa_scene_{scene_id:02d}")
                    self.assertEqual(scene_checkpoint["state"], "done")

    def test_completed_scenes_resume_after_later_scene_failure(self):
        provider = FakeVisionProvider(fail_scene=5)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_dir = root / "001-drzwi-0"
            with StudioStore(root / "studio.db") as store:
                store.upsert_project(self._project())
                self._attach_images(store, project_dir)
                qa = VisualQA(store, output_root=root)
                with self.assertRaises(Exception):
                    qa.run("001", provider)
                self.assertEqual(len(provider.image_calls), 5)
                self.assertEqual(qa.tasks.get_checkpoint("001", "visual_qa")["state"], "failed")
                for scene_id in range(1, 5):
                    self.assertEqual(qa.tasks.get_checkpoint("001", f"visual_qa_scene_{scene_id:02d}")["state"], "done")

                provider.fail_scene = None
                provider.image_calls.clear()
                report, _ = qa.run("001", provider)
                self.assertEqual(report.score, 74)
                self.assertEqual(len(provider.image_calls), 4)
                self.assertIn("Scene 5", provider.image_calls[0]["prompt"])
                self.assertIn("Scene 8", provider.image_calls[-1]["prompt"])

    def test_invalid_first_scene_marks_main_checkpoint_failed(self):
        provider = FakeVisionProvider(fail_scene=1)
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
                scene_checkpoint = qa.tasks.get_checkpoint("001", "visual_qa_scene_01")
                self.assertEqual(scene_checkpoint["state"], "failed")
                self.assertIn("error", scene_checkpoint["metadata"])


if __name__ == "__main__":
    unittest.main()
