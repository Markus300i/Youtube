from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from csp_studio.new_short_wizard import NewShortWizard, WizardValidationError, normalize_wizard_payload
from csp_studio.store import StudioStore


class NewShortWizardTests(unittest.TestCase):
    def _payload(self):
        narration = " ".join(f"word{i}" for i in range(1, 81))
        return {
            "id": "002-zaginiony-pociag",
            "title": "Zaginiony pociąg",
            "fictional": True,
            "narration": narration,
            "scenes": [
                {
                    "id": i,
                    "text": " ".join(f"scena{i}_slowo{word}" for word in range(1, 11)),
                    "prompt": f"Realistyczna polska scena kolejowa numer {i}, cinematic, 9:16",
                }
                for i in range(1, 9)
            ],
        }

    @staticmethod
    def _joined_scene_text(payload):
        return " ".join(scene["text"].strip() for scene in payload["scenes"])

    def test_normalize_overwrites_narration_with_joined_scene_text(self) -> None:
        payload = self._payload()
        payload["narration"] = "zupełnie inny tekst"

        normalized = normalize_wizard_payload(payload)

        self.assertEqual(normalized["narration"], self._joined_scene_text(normalized))
        self.assertEqual(len(normalized["narration"].split()), 80)

    def test_short_explicit_narration_is_ignored_when_scene_text_is_valid(self) -> None:
        payload = self._payload()
        payload["narration"] = " ".join(f"stare_slowo{i}" for i in range(1, 52))

        normalized = normalize_wizard_payload(payload)

        self.assertEqual(len(normalized["narration"].split()), 80)
        self.assertEqual(normalized["narration"], self._joined_scene_text(normalized))

    def test_valid_explicit_narration_does_not_hide_short_scene_text(self) -> None:
        payload = self._payload()
        payload["narration"] = " ".join(f"stare_slowo{i}" for i in range(1, 101))
        word_counts = [7, 7, 7, 6, 6, 6, 6, 6]
        for scene, word_count in zip(payload["scenes"], word_counts):
            scene["text"] = " ".join(
                f"scena{scene['id']}_krotkie{word}" for word in range(1, word_count + 1)
            )

        with self.assertRaisesRegex(
            WizardValidationError,
            r"narration must contain 70-160 words, got 51",
        ):
            normalize_wizard_payload(payload)

    def test_create_writes_yaml_and_sqlite_project(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "studio.db"
            shorts = root / "shorts"
            with StudioStore(db) as store:
                result = NewShortWizard(store, shorts_dir=shorts).create(self._payload())
                scenes = store.list_scenes("002-zaginiony-pociag")
            self.assertEqual(result["scene_count"], 8)
            self.assertTrue((shorts / "002-zaginiony-pociag.yaml").is_file())
            self.assertEqual(len(scenes), 8)
            self.assertTrue(result["project"]["fictional"])

    def test_rejects_non_fictional_project(self) -> None:
        payload = self._payload()
        payload["fictional"] = False
        with tempfile.TemporaryDirectory() as tmp:
            with StudioStore(Path(tmp) / "studio.db") as store:
                with self.assertRaises(WizardValidationError):
                    NewShortWizard(store, shorts_dir=Path(tmp) / "shorts").create(payload)


if __name__ == "__main__":
    unittest.main()
