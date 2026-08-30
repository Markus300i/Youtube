from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from csp_studio.import_short import project_from_short
from csp_studio.store import StudioStore
from csp_studio.visual_bible import VisualBible, VisualBibleEntity

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def _load_generate_scene():
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location("csp_generate_scene_visual_bible", SCRIPTS / "generate_scene.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load scripts/generate_scene.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class VisualBibleGenerationTests(unittest.TestCase):
    def test_execution_prompt_compiles_visual_bible_without_mutating_scene(self) -> None:
        module = _load_generate_scene()
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "studio.db"
            short = {
                "id": "001",
                "title": "Drzwi 0",
                "fictional": True,
                "scenes": [{"id": 1, "text": "tekst", "prompt": "kanoniczny prompt"}],
            }
            with StudioStore(db) as store:
                store.upsert_project(project_from_short(short))
                bible = VisualBible(store)
                bible.upsert(VisualBibleEntity("001", "style", "style", "CSP", prompt_fragment="polski thriller dokumentalny"))
                bible.upsert(VisualBibleEntity("001", "hero", "character", "Mężczyzna", prompt_fragment="mężczyzna w ciemnym płaszczu", reference_asset_path=str(Path(tmp) / "hero.png")))
                bible.assign("001", 1, ["hero"])

            data = {"id": "001", "scenes": [{"id": 1, "prompt": "kanoniczny prompt"}]}
            with patch.dict(os.environ, {"CSP_STUDIO_DB": str(db)}, clear=False):
                result = module._apply_visual_bible(data, 1)

            scene_payload = result["scenes"][0]
            self.assertIn("polski thriller dokumentalny", scene_payload["prompt"])
            self.assertIn("mężczyzna w ciemnym płaszczu", scene_payload["prompt"])
            self.assertTrue(scene_payload["prompt"].endswith("kanoniczny prompt"))
            self.assertEqual(scene_payload["visual_bible_entities"], ["style", "hero"])
            self.assertEqual(len(scene_payload["visual_bible_reference_assets"]), 1)

            with StudioStore(db) as store:
                self.assertEqual(store.get_scene("001", 1).prompt, "kanoniczny prompt")


if __name__ == "__main__":
    unittest.main()
