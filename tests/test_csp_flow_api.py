from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from csp_studio.flow_api import router


class FlowApiTests(unittest.TestCase):
    def test_flow_scripts_are_not_browser_cached(self) -> None:
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)
        for path in ("/flow.js", "/flow-v2.js"):
            response = client.get(path)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIn("no-store", response.headers.get("cache-control", ""))
            self.assertEqual(response.headers.get("pragma"), "no-cache")

    def test_wizard_production_run_and_visual_bible_api(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "studio.db"
            shorts = root / "shorts"
            output = root / "output"
            app = FastAPI()
            app.include_router(router)
            client = TestClient(app)

            words = [f"slowo{i}" for i in range(80)]
            narration = " ".join(words)
            scenes = [
                {
                    "id": i,
                    "text": f"Scena {i} opis narracji",
                    "prompt": f"Prompt sceny {i}",
                    "motion": "static",
                    "continuity_refs": [],
                    "render": {"mode": "generate"},
                }
                for i in range(1, 9)
            ]
            payload = {
                "id": "wizard-api-smoke",
                "title": "Wizard API Smoke",
                "series": "Ciemna Strona Polski",
                "fictional": True,
                "status": "draft",
                "narration": narration,
                "visual_style": "polski thriller dokumentalny",
                "scenes": scenes,
            }

            with patch("csp_studio.flow_api.DB_PATH", db), patch("csp_studio.flow_api.SHORTS_DIR", shorts), patch("csp_studio.flow_api.OUTPUT_ROOT", output):
                created = client.post("/api/wizard/projects", json=payload)
                self.assertEqual(created.status_code, 200, created.text)
                self.assertEqual(created.json()["scene_count"], 8)
                self.assertTrue((shorts / "wizard-api-smoke.yaml").is_file())

                status = client.get("/api/projects/wizard-api-smoke/production-run")
                self.assertEqual(status.status_code, 200, status.text)
                self.assertEqual(status.json()["run"]["state"], "idle")
                self.assertEqual(status.json()["agent"]["next_action"], "complete_images")

                started = client.post("/api/projects/wizard-api-smoke/production-run/start")
                self.assertEqual(started.status_code, 200, started.text)
                self.assertEqual(started.json()["reason"], "complete_images")
                self.assertEqual(started.json()["run"]["state"], "blocked")

                style = client.post(
                    "/api/projects/wizard-api-smoke/visual-bible/entities",
                    json={"entity_key": "global-style", "kind": "style", "name": "Styl", "prompt_fragment": "desaturated documentary look"},
                )
                self.assertEqual(style.status_code, 200, style.text)
                hero = client.post(
                    "/api/projects/wizard-api-smoke/visual-bible/entities",
                    json={"entity_key": "hero", "kind": "character", "name": "Bohater", "prompt_fragment": "dark wool coat"},
                )
                self.assertEqual(hero.status_code, 200, hero.text)

                assigned = client.put(
                    "/api/projects/wizard-api-smoke/scenes/1/visual-bible",
                    json={"entity_keys": ["hero"]},
                )
                self.assertEqual(assigned.status_code, 200, assigned.text)
                result = assigned.json()
                self.assertEqual(result["canonical_prompt"], "Prompt sceny 1")
                self.assertIn("desaturated documentary look", result["compiled_prompt"])
                self.assertIn("dark wool coat", result["compiled_prompt"])


if __name__ == "__main__":
    unittest.main()
