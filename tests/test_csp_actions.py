from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import yaml

from csp_studio.action_api import ACTION_META, MANUAL_ACTIONS, _quick_snapshot


class StudioActionsTests(unittest.TestCase):
    def test_quick_snapshot_forces_zimage_and_skips_flux_for_target_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "current.yaml"
            target = root / "quick.yaml"
            source.write_text(
                yaml.safe_dump(
                    {
                        "id": "001",
                        "title": "Drzwi 0",
                        "image_model": "flux2-klein",
                        "continuity": {"global": "keep same basement"},
                        "scenes": [
                            {
                                "id": 1,
                                "prompt": "master door",
                                "render": {"mode": "generate"},
                            },
                            {
                                "id": 4,
                                "prompt": "tenant listening",
                                "continuity_refs": ["basement", "basement_door", "tenant"],
                                "render": {
                                    "mode": "flux_edit",
                                    "reference_scene": 1,
                                    "edit_rects": [[0.03, 0.30, 0.49, 0.96]],
                                },
                            },
                        ],
                    },
                    allow_unicode=True,
                    sort_keys=False,
                ),
                encoding="utf-8",
            )

            _quick_snapshot(source, 4, target)
            data = yaml.safe_load(target.read_text(encoding="utf-8"))
            self.assertEqual(data["image_model"], "z-image-turbo")
            scene1 = next(scene for scene in data["scenes"] if scene["id"] == 1)
            scene4 = next(scene for scene in data["scenes"] if scene["id"] == 4)
            self.assertEqual(scene1["render"], {"mode": "generate"})
            self.assertEqual(scene4["render"], {"mode": "generate"})
            self.assertEqual(scene4["prompt"], "tenant listening")
            self.assertEqual(scene4["continuity_refs"], ["basement", "basement_door", "tenant"])
            self.assertEqual(data["continuity"]["global"], "keep same basement")

    def test_manual_action_map_uses_existing_pipeline_stages(self) -> None:
        self.assertEqual(MANUAL_ACTIONS["tts"], ("tts", "gpu"))
        self.assertEqual(MANUAL_ACTIONS["captions"], ("captions", "gpu"))
        self.assertEqual(MANUAL_ACTIONS["sound_design"], ("sound_design", "cpu"))
        self.assertEqual(MANUAL_ACTIONS["visual_qa"], ("visual_qa", "network"))
        self.assertEqual(MANUAL_ACTIONS["opencut_export"], ("opencut_export", "io"))
        self.assertEqual(MANUAL_ACTIONS["render_final"], ("render_final", "gpu"))

    def test_manual_action_dependencies_protect_pipeline_order(self) -> None:
        self.assertEqual(ACTION_META["tts"]["requires"], ())
        self.assertEqual(ACTION_META["captions"]["requires"], ("tts",))
        self.assertEqual(ACTION_META["sound_design"]["requires"], ("tts",))
        self.assertEqual(ACTION_META["visual_qa"]["requires"], ("active_images",))
        self.assertEqual(
            ACTION_META["opencut_export"]["requires"],
            ("active_images", "tts", "captions", "sound_design"),
        )
        self.assertEqual(
            ACTION_META["render_final"]["requires"],
            ("active_images", "tts", "captions", "sound_design", "scene_review"),
        )


if __name__ == "__main__":
    unittest.main()
