from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from csp_studio.import_short import project_from_short
from csp_studio.store import StudioStore
from csp_studio.visual_bible import VisualBible, VisualBibleEntity


class VisualBibleTests(unittest.TestCase):
    def test_assign_and_compile_scene_prompt_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "studio.db"
            with StudioStore(db) as store:
                store.upsert_project(
                    project_from_short(
                        {
                            "id": "001",
                            "title": "Drzwi 0",
                            "fictional": True,
                            "scenes": [
                                {
                                    "id": 1,
                                    "text": "Narracja sceny.",
                                    "prompt": "Mężczyzna stoi przy drzwiach.",
                                    "continuity_refs": ["basement", "tenant"],
                                }
                            ],
                        }
                    )
                )
                bible = VisualBible(store)
                bible.upsert(
                    VisualBibleEntity(
                        "001",
                        "global_style",
                        "style",
                        "CSP documentary style",
                        prompt_fragment="realistyczny polski thriller dokumentalny, desaturated colors, 9:16",
                    )
                )
                bible.upsert(
                    VisualBibleEntity(
                        "001",
                        "basement",
                        "location",
                        "Piwnica",
                        prompt_fragment="ta sama wilgotna piwnica z zielonkawymi ścianami",
                    )
                )
                bible.upsert(
                    VisualBibleEntity(
                        "001",
                        "tenant",
                        "character",
                        "Lokator",
                        prompt_fragment="ten sam mężczyzna około 35 lat, ciemna kurtka",
                    )
                )
                scene = store.get_scene("001", 1)
                bible.sync_scene_continuity_refs(scene)
                assigned = bible.assigned("001", 1)
                compiled = bible.compile_prompt(scene)

            self.assertEqual({item.entity_key for item in assigned}, {"basement", "tenant"})
            self.assertIn("realistyczny polski thriller dokumentalny", compiled)
            self.assertIn("ta sama wilgotna piwnica", compiled)
            self.assertIn("ten sam mężczyzna", compiled)
            self.assertTrue(compiled.endswith("Mężczyzna stoi przy drzwiach."))


if __name__ == "__main__":
    unittest.main()
