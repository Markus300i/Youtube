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

    def __init__(self, *, fail_pair: str | None = None):
        self.image_calls: list[dict] = []
        self.chat_calls: list[dict] = []
        self.fail_pair = fail_pair

    def analyze_images(self, prompt, image_paths, *, model=None, temperature=0.1, max_tokens=1600):
        paths = list(image_paths)
        self.image_calls.append({"prompt": prompt, "paths": paths, "max_tokens": max_tokens})
        pair = None
        for candidate in ("Scene 1 then Scene 2", "Scene 3 then Scene 4", "Scene 5 then Scene 6", "Scene 7 then Scene 8"):
            if candidate in prompt:
                pair = candidate
                break
        if self.fail_pair and pair == self.fail_pair:
            return ProviderResponse(provider=self.name, model="fake-vlm", text="not json")
        ids = [int(Path(path).stem.split("-")[1]) for path in paths]
        payload = {
            "pair_score": 80 - ids[0],
            "warnings": [f"Pair {ids[0]}-{ids[1]} warning"],
            "continuity": [f"Pair {ids[0]}-{ids[1]} continuity"],
            "monotony": [f"Pair {ids[0]}-{ids[1]} monotony"],
            "scene_notes": [
                {
                    "scene_id": ids[1],
                    "severity": "warning",
                    "issue": f"Issue scene {ids[1]}",
                    "recommendation": "Change framing",
                }
            ],
        }
        return ProviderResponse(provider=self.name, model="fake-vlm", text=json.dumps(payload))

    def chat(self, messages, *, model=None, temperature=0.1, max_tokens=1400):
        self.chat_calls.append({"messages": messages, "max_tokens": max_tokens})
        payload = {
            "score": 74,
            "summary": "Good continuity but the middle repeats framing.",
            "warnings": ["Scenes 3-5 are too similar."],
            "continuity": ["Door orientation is stable."],
            "monotony": ["Middle section repeats similar framing."],
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

    def test_visual_qa_uses_four_image_pairs_then_text_aggregation(self):
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
                self.assertEqual(report.strategy, "pairwise_v1")
                self.assertEqual(len(provider.image_calls), 4)
                self.assertEqual(len(provider.chat_calls), 1)
                self.assertTrue(all(len(call["paths"]) == 2 for call in provider.image_calls))
                self.assertTrue(all(call["max_tokens"] == 900 for call in provider.image_calls))
                self.assertIn("Scene 3 then Scene 4", provider.image_calls[1]["prompt"])
                self.assertIn("Narracja sceny 4", provider.image_calls[1]["prompt"])

                for call in provider.image_calls:
                    for path_str in call["paths"]:
                        path = Path(path_str)
                        self.assertTrue(path.is_file())
                        self.assertEqual(path.suffix.lower(), ".jpg")
                        with Image.open(path) as image:
                            self.assertLessEqual(image.width, 360)
                            self.assertLessEqual(image.height, 640)

                self.assertTrue(report_path.is_file())
                saved = json.loads(report_path.read_text(encoding="utf-8"))
                self.assertEqual(saved["score"], 74)
                self.assertEqual(saved["strategy"], "pairwise_v1")
                checkpoint = qa.tasks.get_checkpoint("001", "visual_qa")
                self.assertEqual(checkpoint["state"], "done")
                self.assertEqual(checkpoint["metadata"]["pairs"], 4)
                for first, second in ((1, 2), (3, 4), (5, 6), (7, 8)):
                    pair_checkpoint = qa.tasks.get_checkpoint("001", f"visual_qa_pair_{first:02d}_{second:02d}")
                    self.assertEqual(pair_checkpoint["state"], "done")

    def test_completed_pairs_resume_after_later_pair_failure(self):
        provider = FakeVisionProvider(fail_pair="Scene 5 then Scene 6")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_dir = root / "001-drzwi-0"
            with StudioStore(root / "studio.db") as store:
                store.upsert_project(self._project())
                self._attach_images(store, project_dir)
                qa = VisualQA(store, output_root=root)
                with self.assertRaises(Exception):
                    qa.run("001", provider)
                self.assertEqual(len(provider.image_calls), 3)
                self.assertEqual(qa.tasks.get_checkpoint("001", "visual_qa")["state"], "failed")
                self.assertEqual(qa.tasks.get_checkpoint("001", "visual_qa_pair_01_02")["state"], "done")
                self.assertEqual(qa.tasks.get_checkpoint("001", "visual_qa_pair_03_04")["state"], "done")

                provider.fail_pair = None
                provider.image_calls.clear()
                report, _ = qa.run("001", provider)
                self.assertEqual(report.score, 74)
                self.assertEqual(len(provider.image_calls), 2)
                self.assertIn("Scene 5 then Scene 6", provider.image_calls[0]["prompt"])
                self.assertIn("Scene 7 then Scene 8", provider.image_calls[1]["prompt"])

    def test_invalid_first_pair_marks_main_checkpoint_failed(self):
        provider = FakeVisionProvider(fail_pair="Scene 1 then Scene 2")
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
                pair_checkpoint = qa.tasks.get_checkpoint("001", "visual_qa_pair_01_02")
                self.assertEqual(pair_checkpoint["state"], "failed")
                self.assertIn("error", pair_checkpoint["metadata"])


if __name__ == "__main__":
    unittest.main()
