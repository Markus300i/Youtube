from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from csp_studio.agent_one import AgentOne
from csp_studio.asset_manager import AssetManager
from csp_studio.import_short import project_from_short
from csp_studio.providers.base import ProviderResponse
from csp_studio.store import StudioStore


class FakeChatProvider:
    name = "fake"

    def __init__(self):
        self.messages = None

    def chat(self, messages, *, model=None, temperature=0.2, max_tokens=2048):
        self.messages = messages
        return ProviderResponse(provider="fake", model="fake-model", text="Następny krok: review.")


class AgentOneTests(unittest.TestCase):
    def _project(self):
        return project_from_short(
            {
                "id": "001",
                "title": "Drzwi 0",
                "series": "Nie Otwieraj",
                "scenes": [
                    {"id": index, "text": f"Scena {index}", "prompt": f"Prompt {index}", "motion": "static"}
                    for index in range(1, 9)
                ],
            }
        )

    def _attach_images(self, store: StudioStore, project_dir: Path, *, approve: bool = False):
        images_dir = project_dir / "images"
        images_dir.mkdir(parents=True, exist_ok=True)
        manager = AssetManager(store)
        for index in range(1, 9):
            path = images_dir / f"scene-{index:02d}.png"
            Image.new("RGB", (9, 16), "black").save(path)
            manager.register_asset("001", index, path, source="gpt-browser-manual")
            if approve:
                manager.approve_scene("001", index)

    def _write_production_artifacts(self, project_dir: Path):
        audio = project_dir / "audio"
        audio.mkdir(parents=True, exist_ok=True)
        (audio / "voice.wav").write_bytes(b"voice")
        (audio / "final_mix.wav").write_bytes(b"mix")
        cursor = 0.0
        scenes = []
        for index in range(1, 9):
            scenes.append({"id": index, "start": cursor, "end": cursor + 1.0, "duration": 1.0})
            cursor += 1.0
        (audio / "tts-timings.json").write_text(json.dumps({"duration": 8.0, "scenes": scenes}), encoding="utf-8")
        (project_dir / "subtitles.ass").write_text("[Script Info]", encoding="utf-8")

    def test_missing_images_are_first_blocking_action(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with StudioStore(root / "studio.db") as store:
                store.upsert_project(self._project())
                report = AgentOne(store, output_root=root).inspect("001")
                self.assertFalse(report.assets_ready)
                self.assertEqual(report.next_action, "complete_images")
                self.assertFalse(next(check for check in report.checks if check.key == "active_images").ok)

    def test_ready_assets_progress_to_tts_then_visual_qa_then_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_dir = root / "001-drzwi-0"
            with StudioStore(root / "studio.db") as store:
                store.upsert_project(self._project())
                self._attach_images(store, project_dir)
                agent = AgentOne(store, output_root=root)
                report = agent.inspect("001")
                self.assertTrue(report.assets_ready)
                self.assertEqual(report.next_action, "generate_tts")

                self._write_production_artifacts(project_dir)
                report = agent.inspect("001")
                self.assertTrue(report.production_ready)
                self.assertEqual(report.next_action, "visual_qa")

                agent.tasks.set_checkpoint("001", "visual_qa", "done", metadata={"score": 82})
                report = agent.inspect("001")
                self.assertEqual(report.next_action, "review_scenes")
                self.assertFalse(report.final_ready)

                manager = AssetManager(store)
                for index in range(1, 9):
                    manager.approve_scene("001", index)
                report = agent.inspect("001")
                self.assertTrue(report.final_ready)
                self.assertEqual(report.next_action, "export_opencut")

    def test_enqueue_next_is_idempotent_for_active_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_dir = root / "001-drzwi-0"
            with StudioStore(root / "studio.db") as store:
                store.upsert_project(self._project())
                self._attach_images(store, project_dir)
                agent = AgentOne(store, output_root=root)
                first = agent.enqueue_next("001")
                second = agent.enqueue_next("001")
                self.assertTrue(first["queued"])
                self.assertEqual(first["task"]["stage"], "tts")
                self.assertFalse(second["queued"])
                self.assertEqual(second["reason"], "already_queued")
                self.assertEqual(len(agent.tasks.list("001", "queued")), 1)

    def test_ai_explanation_receives_verified_state_but_cannot_change_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with StudioStore(root / "studio.db") as store:
                store.upsert_project(self._project())
                agent = AgentOne(store, output_root=root)
                provider = FakeChatProvider()
                result = agent.explain("001", provider)
                self.assertEqual(result["report"]["next_action"], "complete_images")
                self.assertFalse(result["report"]["final_ready"])
                self.assertEqual(result["assistant"]["provider"], "fake")
                sent = json.dumps(provider.messages, ensure_ascii=False)
                self.assertIn("complete_images", sent)
                self.assertIn("final_ready", sent)


if __name__ == "__main__":
    unittest.main()
