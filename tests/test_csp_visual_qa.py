from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from csp_studio.asset_manager import AssetManager
from csp_studio.import_short import project_from_short
from csp_studio.providers.base import ProviderError, ProviderResponse
from csp_studio.store import StudioStore
from csp_studio.visual_qa import VisualQA


class FakeVisionProvider:
    name = "fake_vision"

    def __init__(self, *, empty_scene: int | None = None, invalid_aggregate: bool = False, aggregate_error: bool = False):
        self.image_calls: list[dict] = []
        self.chat_calls: list[dict] = []
        self.empty_scene = empty_scene
        self.invalid_aggregate = invalid_aggregate
        self.aggregate_error = aggregate_error

    def analyze_images(self, prompt, image_paths, *, model=None, temperature=0.1, max_tokens=1600):
        paths = list(image_paths)
        self.image_calls.append({"prompt": prompt, "paths": paths, "max_tokens": max_tokens})
        scene_id = int(Path(paths[0]).stem.split("-")[1])
        if self.empty_scene == scene_id:
            return ProviderResponse(provider=self.name, model="fake-vlm", text="")
        text = (
            f"Scene {scene_id}: medium documentary framing in a basement. "
            f"Dominant subject is subject-{scene_id}. Dark door remains a useful continuity cue. "
            "Image is readable on mobile and broadly supports the narration."
        )
        return ProviderResponse(provider=self.name, model="fake-vlm", text=text)

    def chat(self, messages, *, model=None, temperature=0.1, max_tokens=1200):
        self.chat_calls.append({"messages": messages, "max_tokens": max_tokens})
        if self.aggregate_error:
            raise ProviderError("NVIDIA NIM HTTP 410: retired model")
        if self.invalid_aggregate:
            return ProviderResponse(provider=self.name, model="fake-text", text="plain prose, not json")
        payload = {
            "score": 74,
            "summary": "Good continuity but scenes 3 and 4 repeat framing.",
            "warnings": ["Scene 4 repeats Scene 3."],
            "continuity": ["Door appearance remains stable."],
            "monotony": ["Scenes 3 and 4 share similar framing."],
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

    def test_visual_qa_uses_plain_text_scene_reviews_then_text_aggregation(self):
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
                self.assertEqual(report.strategy, "single_scene_prose_v2")
                self.assertEqual(len(provider.image_calls), 8)
                self.assertEqual(len(provider.chat_calls), 1)
                self.assertTrue(all(len(call["paths"]) == 1 for call in provider.image_calls))
                self.assertTrue(all(call["max_tokens"] == 420 for call in provider.image_calls))
                self.assertIn("Do not output JSON", provider.image_calls[0]["prompt"])

                saved = json.loads(report_path.read_text(encoding="utf-8"))
                self.assertEqual(saved["score"], 74)
                self.assertEqual(saved["strategy"], "single_scene_prose_v2")
                for scene_id in range(1, 9):
                    scene_file = project_dir / "qa" / "scenes" / f"scene-{scene_id:02d}.json"
                    scene_data = json.loads(scene_file.read_text(encoding="utf-8"))
                    self.assertTrue(scene_data["review_text"])
                    checkpoint = qa.tasks.get_checkpoint("001", f"visual_qa_scene_{scene_id:02d}")
                    self.assertEqual(checkpoint["state"], "done")

    def test_completed_scenes_resume_after_empty_later_scene(self):
        provider = FakeVisionProvider(empty_scene=5)
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
                for scene_id in range(1, 5):
                    self.assertEqual(qa.tasks.get_checkpoint("001", f"visual_qa_scene_{scene_id:02d}")["state"], "done")

                provider.empty_scene = None
                provider.image_calls.clear()
                report, _ = qa.run("001", provider)
                self.assertEqual(report.score, 74)
                self.assertEqual(len(provider.image_calls), 4)

    def test_invalid_aggregate_falls_back_instead_of_failing_visual_qa(self):
        provider = FakeVisionProvider(invalid_aggregate=True)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_dir = root / "001-drzwi-0"
            with StudioStore(root / "studio.db") as store:
                store.upsert_project(self._project())
                self._attach_images(store, project_dir)
                qa = VisualQA(store, output_root=root)
                report, _ = qa.run("001", provider)
                self.assertEqual(report.strategy, "single_scene_prose_v2")
                self.assertEqual(report.score, 100)
                self.assertIn("Structured aggregate unavailable", report.summary)
                self.assertEqual(qa.tasks.get_checkpoint("001", "visual_qa")["state"], "done")

    def test_aggregate_http_failure_falls_back_instead_of_failing_visual_qa(self):
        provider = FakeVisionProvider(aggregate_error=True)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_dir = root / "001-drzwi-0"
            with StudioStore(root / "studio.db") as store:
                store.upsert_project(self._project())
                self._attach_images(store, project_dir)
                qa = VisualQA(store, output_root=root)
                report, _ = qa.run("001", provider)
                self.assertEqual(report.score, 100)
                self.assertEqual(qa.tasks.get_checkpoint("001", "visual_qa")["state"], "done")
                self.assertTrue(any("410" in warning for warning in report.warnings))


if __name__ == "__main__":
    unittest.main()
