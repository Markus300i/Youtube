from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from csp_studio.new_short_wizard import NewShortWizard, WizardValidationError
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
                    "text": f"Scena {i} prowadzi historię dalej.",
                    "prompt": f"Realistyczna polska scena kolejowa numer {i}, cinematic, 9:16",
                }
                for i in range(1, 9)
            ],
        }

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
