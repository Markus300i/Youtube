from __future__ import annotations

import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

# transcribe.py only needs the real faster-whisper package inside main().
# Keep unit tests for pure subtitle helpers independent of GPU/model installs.
if "faster_whisper" not in sys.modules:
    fake_whisper = types.ModuleType("faster_whisper")
    fake_whisper.WhisperModel = object
    sys.modules["faster_whisper"] = fake_whisper


def load_script(name: str):
    path = SCRIPTS / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"csp_test_{name}", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


generate_images = load_script("generate_images")
transcribe = load_script("transcribe")


class ImageResumeTests(unittest.TestCase):
    def short(self):
        return {
            "id": "test-short",
            "title": "Test",
            "image_model": "z-image-turbo",
            "scenes": [{"id": index, "prompt": f"Scene {index}"} for index in range(1, 9)],
        }

    def test_complete_eight_images_are_detected(self):
        with tempfile.TemporaryDirectory() as temp:
            images = Path(temp)
            for index in range(1, 9):
                (images / f"scene-{index:02d}.png").write_bytes(b"png")
            self.assertTrue(generate_images.complete_scene_images(self.short(), images))

    def test_missing_image_does_not_trigger_complete_skip(self):
        with tempfile.TemporaryDirectory() as temp:
            images = Path(temp)
            for index in range(1, 8):
                (images / f"scene-{index:02d}.png").write_bytes(b"png")
            self.assertFalse(generate_images.complete_scene_images(self.short(), images))

    def test_main_returns_before_comfy_when_all_images_exist(self):
        with tempfile.TemporaryDirectory() as temp:
            project_dir = Path(temp)
            images = project_dir / "images"
            images.mkdir()
            for index in range(1, 9):
                (images / f"scene-{index:02d}.png").write_bytes(b"png")

            short = self.short()
            with (
                patch.object(generate_images, "load_yaml", return_value=short),
                patch.object(generate_images, "short_output_dir", return_value=project_dir),
                patch.object(
                    generate_images,
                    "wait_for_comfy",
                    side_effect=AssertionError("ComfyUI must not be contacted"),
                ),
                patch.object(sys, "argv", ["generate_images.py", "short.yaml"]),
            ):
                generate_images.main()


class SubtitleLayoutTests(unittest.TestCase):
    def test_wrap_is_never_more_than_two_lines(self):
        text = "Administrator sprawdził stare plany całego budynku bardzo dokładnie"
        lines = transcribe.wrap_subtitle_text(text, max_chars=32, max_lines=2)
        self.assertLessEqual(len(lines), 2)

    def test_balanced_normal_caption_stays_inside_character_limit(self):
        text = "Na końcu stały identyczne drzwi do jego mieszkania"
        lines = transcribe.wrap_subtitle_text(text, max_chars=32, max_lines=2)
        self.assertLessEqual(len(lines), 2)
        self.assertTrue(all(len(line) <= 32 for line in lines))

    def test_ass_uses_vertical_safe_area_and_explicit_line_breaks(self):
        chunks = [{"start": 1.0, "end": 2.5, "text": "Administrator sprawdził plany starego budynku"}]
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "subtitles.ass"
            transcribe.write_ass(target, chunks, uppercase=True, max_chars=28)
            content = target.read_text(encoding="utf-8-sig")

        self.assertIn("Style: CSP,Arial,60", content)
        self.assertIn(",96,96,190,1", content)
        dialogue = next(line for line in content.splitlines() if line.startswith("Dialogue:"))
        self.assertIn(r"\N", dialogue)
        self.assertLessEqual(len(dialogue.split(r"\N")), 2)


if __name__ == "__main__":
    unittest.main()
