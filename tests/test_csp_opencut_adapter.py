from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from csp_studio.asset_manager import AssetManager
from csp_studio.import_short import project_from_short
from csp_studio.opencut_adapter import build_manifest, export_manifest
from csp_studio.store import StudioStore


class OpenCutAdapterTests(unittest.TestCase):
    def test_exports_ordered_timeline_from_active_assets_and_tts_timings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "output"
            output.mkdir()
            db = output / "csp-studio.db"
            project_dir = output / "001-drzwi-0"
            images_dir = project_dir / "images"
            audio_dir = project_dir / "audio"
            images_dir.mkdir(parents=True)
            audio_dir.mkdir(parents=True)

            project = project_from_short(
                {
                    "id": "001",
                    "title": "Drzwi 0",
                    "series": "Nie Otwieraj",
                    "scenes": [
                        {"id": 1, "text": "Pierwsza", "prompt": "Door", "motion": "slow_push"},
                        {"id": 2, "text": "Druga", "prompt": "Detail", "motion": "pan_right"},
                    ],
                }
            )

            first = images_dir / "scene-01.png"
            second = images_dir / "scene-02.png"
            Image.new("RGB", (90, 160), "black").save(first)
            Image.new("RGB", (90, 160), "white").save(second)
            (audio_dir / "final_mix.wav").write_bytes(b"fake")
            (audio_dir / "tts-timings.json").write_text(
                json.dumps(
                    {
                        "duration": 7.5,
                        "scenes": [
                            {"id": 1, "start": 0.0, "end": 3.0, "duration": 3.0},
                            {"id": 2, "start": 3.0, "end": 7.5, "duration": 4.5},
                        ],
                    }
                ),
                encoding="utf-8",
            )

            old_output = os.environ.get("CSP_OUTPUT_DIR")
            os.environ["CSP_OUTPUT_DIR"] = str(output)
            try:
                import csp_studio.opencut_adapter as adapter

                adapter.OUTPUT_ROOT = output.resolve()
                with StudioStore(db) as store:
                    store.upsert_project(project)
                    manager = AssetManager(store)
                    manager.register_asset("001", 1, first, source="gpt-browser-manual")
                    manager.register_asset("001", 2, second, source="gpt-browser-manual")
                    manifest = build_manifest(store, "001")

                    self.assertEqual(manifest["format"], "csp-opencut-interchange/1")
                    self.assertEqual(manifest["project"]["canvas"], {"width": 1080, "height": 1920})
                    self.assertEqual(manifest["project"]["fps"], 30)
                    self.assertEqual(manifest["project"]["duration"], 7.5)
                    clips = manifest["timeline"]["main_video"]["elements"]
                    self.assertEqual([clip["id"] for clip in clips], ["scene-01", "scene-02"])
                    self.assertEqual([clip["duration"] for clip in clips], [3.0, 4.5])
                    self.assertEqual(clips[0]["motion_intent"]["kind"], "transform")
                    self.assertEqual(clips[1]["motion_intent"]["kind"], "transform")
                    self.assertEqual(clips[0]["source_provider"], "gpt-browser-manual")
                    self.assertEqual(manifest["timeline"]["audio"][0]["role"], "master")

                    out = export_manifest(store, "001")
                    self.assertTrue(out.is_file())
                    saved = json.loads(out.read_text(encoding="utf-8"))
                    self.assertEqual(saved["project"]["duration"], 7.5)
            finally:
                if old_output is None:
                    os.environ.pop("CSP_OUTPUT_DIR", None)
                else:
                    os.environ["CSP_OUTPUT_DIR"] = old_output

    def test_missing_active_image_fails_instead_of_inventing_clip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "output"
            output.mkdir()
            db = output / "csp-studio.db"
            project_dir = output / "001-drzwi-0" / "audio"
            project_dir.mkdir(parents=True)
            (project_dir / "tts-timings.json").write_text(
                json.dumps({"duration": 1.0, "scenes": [{"id": 1, "start": 0.0, "end": 1.0, "duration": 1.0}]}),
                encoding="utf-8",
            )
            project = project_from_short(
                {"id": "001", "title": "Drzwi 0", "scenes": [{"id": 1, "text": "A", "prompt": "A"}]}
            )

            import csp_studio.opencut_adapter as adapter

            adapter.OUTPUT_ROOT = output.resolve()
            with StudioStore(db) as store:
                store.upsert_project(project)
                with self.assertRaises(RuntimeError):
                    build_manifest(store, "001")


if __name__ == "__main__":
    unittest.main()
