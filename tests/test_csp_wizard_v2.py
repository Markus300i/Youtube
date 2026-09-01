from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from csp_studio.providers.base import ProviderResponse
from csp_studio.store import StudioStore
from csp_studio.visual_bible import VisualBible
from csp_studio.wizard_v2 import WizardV2, create_reviewed_wizard_v2


class FakeProvider:
    name = "fake"

    def __init__(self, payload: dict):
        self.payload = payload
        self.calls = 0

    def chat(self, messages, *, model=None, temperature=0.2, max_tokens=2048):
        self.calls += 1
        return ProviderResponse(
            provider="fake",
            model="fake-model",
            text=json.dumps(self.payload, ensure_ascii=False),
            usage={"total_tokens": 123},
        )


class SequencedFakeProvider(FakeProvider):
    def __init__(self, payloads: list[dict]):
        super().__init__(payloads[-1])
        self.payloads = payloads

    def chat(self, messages, *, model=None, temperature=0.2, max_tokens=2048):
        index = min(self.calls, len(self.payloads) - 1)
        self.payload = self.payloads[index]
        return super().chat(messages, model=model, temperature=temperature, max_tokens=max_tokens)


class ResilientFakeProvider(FakeProvider):
    def __init__(self, payload: dict):
        super().__init__(payload)
        self.request_timeout = None
        self.retries = None
        self.temperature = None
        self.max_tokens = None

    def chat(
        self,
        messages,
        *,
        model=None,
        temperature=0.2,
        max_tokens=2048,
        request_timeout=None,
        retries=0,
    ):
        self.request_timeout = request_timeout
        self.retries = retries
        self.temperature = temperature
        self.max_tokens = max_tokens
        return super().chat(messages, model=model, temperature=temperature, max_tokens=max_tokens)


class WizardV2Tests(unittest.TestCase):
    def _payload(self) -> dict:
        narration = " ".join(f"słowo{i}" for i in range(1, 81))
        return {
            "project": {
                "id": "ai-short",
                "title": "Zamknięty peron",
                "series": "Ciemna Strona Polski",
                "fictional": True,
                "status": "draft",
                "narration": narration,
                "visual_style": "fotorealistyczny polski thriller dokumentalny, 9:16",
                "scenes": [
                    {
                        "id": scene_id,
                        "text": f"Narracja sceny {scene_id}",
                        "prompt": f"Fikcyjna scena {scene_id} na małej polskiej stacji, photorealistic, 9:16",
                        "motion": "static",
                        "continuity_refs": ["night_guard", "station_platform"],
                        "render": {"mode": "generate"},
                    }
                    for scene_id in range(1, 9)
                ],
            },
            "visual_bible": {
                "entities": [
                    {
                        "entity_key": "global_style",
                        "kind": "style",
                        "name": "Global Style",
                        "prompt_fragment": "fotorealistyczny polski thriller dokumentalny, stonowane kolory, 9:16",
                    },
                    {
                        "entity_key": "night_guard",
                        "kind": "character",
                        "name": "Nocny strażnik",
                        "prompt_fragment": "ten sam fikcyjny strażnik, około pięćdziesięciu lat, granatowy płaszcz",
                    },
                    {
                        "entity_key": "station_platform",
                        "kind": "location",
                        "name": "Opuszczony peron",
                        "prompt_fragment": "mały polski dworzec nocą, mokry peron, jedna zimna latarnia",
                    },
                ],
                "assignments": {
                    str(scene_id): ["night_guard", "station_platform"]
                    for scene_id in range(1, 9)
                },
            },
        }

    def test_draft_is_validated_and_shot_audited(self) -> None:
        result = WizardV2(FakeProvider(self._payload())).draft(
            "Strażnik odkrywa peron, którego nie ma w rozkładzie.",
            project_id="wizard-v2-smoke",
        ).to_dict()

        self.assertEqual(result["draft"]["id"], "wizard-v2-smoke")
        self.assertTrue(result["draft"]["fictional"])
        self.assertEqual(len(result["draft"]["scenes"]), 8)
        self.assertEqual(result["shot_audit"]["score"], 100)
        self.assertTrue(result["shot_audit"]["ok"])
        self.assertEqual(result["provider"]["name"], "fake")
        self.assertEqual(result["provider"]["repairs"], 0)
        self.assertEqual(len(result["visual_bible"]["entities"]), 3)

    def test_invalid_scene_count_is_repaired_once(self) -> None:
        invalid = self._payload()
        invalid["project"]["scenes"] = invalid["project"]["scenes"][:7]
        provider = SequencedFakeProvider([invalid, self._payload()])

        result = WizardV2(provider).draft(
            "Strażnik odkrywa peron, którego nie ma w rozkładzie.",
            project_id="wizard-v2-repair",
        ).to_dict()

        self.assertEqual(provider.calls, 2)
        self.assertEqual(len(result["draft"]["scenes"]), 8)
        self.assertEqual(result["provider"]["repairs"], 1)

    def test_provider_with_resilience_parameters_gets_wizard_timeout_and_retry(self) -> None:
        provider = ResilientFakeProvider(self._payload())
        WizardV2(provider).draft("Fikcyjny test timeoutu Wizarda V2.")
        self.assertEqual(provider.request_timeout, 180.0)
        self.assertEqual(provider.retries, 1)
        self.assertEqual(provider.temperature, 0.35)
        self.assertEqual(provider.max_tokens, 3800)

    def test_reviewed_create_persists_project_and_visual_bible(self) -> None:
        draft = WizardV2(FakeProvider(self._payload())).draft(
            "Strażnik odkrywa peron, którego nie ma w rozkładzie.",
            project_id="wizard-v2-create",
        ).to_dict()

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shorts = root / "shorts"
            db = root / "studio.db"
            with StudioStore(db) as store:
                result = create_reviewed_wizard_v2(store, shorts_dir=shorts, envelope=draft)
                self.assertEqual(result["scene_count"], 8)
                self.assertEqual(result["shot_audit"]["score"], 100)
                self.assertIsNotNone(store.get_scene("wizard-v2-create", 8))
                bible = VisualBible(store)
                self.assertEqual(len(bible.list("wizard-v2-create")), 3)
                assigned = [item.entity_key for item in bible.assigned("wizard-v2-create", 1)]
                self.assertEqual(assigned, ["night_guard", "station_platform"])
                scene = store.get_scene("wizard-v2-create", 1)
                assert scene is not None
                context = bible.prompt_context("wizard-v2-create", 1)
                self.assertIn("fotorealistyczny polski thriller", context)
                self.assertNotIn("fotorealistyczny polski thriller", scene.prompt)

            self.assertTrue((shorts / "wizard-v2-create.yaml").is_file())


if __name__ == "__main__":
    unittest.main()
