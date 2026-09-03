from __future__ import annotations

import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from csp_studio.providers.base import ProviderResponse
from csp_studio.store import StudioStore
from csp_studio.visual_bible import VisualBible
from csp_studio.wizard_v2 import (
    WizardV2,
    WizardV2Error,
    create_reviewed_wizard_v2,
    normalize_whitespace,
    split_narration_into_scenes,
)


class SequencedFakeProvider:
    name = "fake"

    def __init__(self, payloads: list[dict]):
        self.payloads = payloads
        self.calls = 0
        self.call_args: list[dict] = []

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
        index = min(self.calls, len(self.payloads) - 1)
        payload = self.payloads[index]
        self.calls += 1
        self.call_args.append(
            {
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "request_timeout": request_timeout,
                "retries": retries,
            }
        )
        return ProviderResponse(
            provider="fake",
            model="fake-model",
            text=json.dumps(payload, ensure_ascii=False),
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        )


class WizardV2Tests(unittest.TestCase):
    @staticmethod
    def _narration(word_count: int, *, sentence_every: int | None = 12) -> str:
        words = []
        for index in range(1, word_count + 1):
            token = f"slowo{index}"
            if sentence_every and index % sentence_every == 0:
                token += "."
            words.append(token)
        return " ".join(words)

    def _story(self, word_count: int = 100) -> dict:
        return {
            "title": "Zamknięty peron",
            "narration": self._narration(word_count),
        }

    @staticmethod
    def _visual() -> dict:
        return {
            "visual_style": "fotorealistyczny polski thriller dokumentalny, 9:16",
            "scenes": [
                {
                    "id": scene_id,
                    "prompt": f"Fikcyjna scena {scene_id} na małej polskiej stacji, photorealistic, 9:16",
                    "motion": "static",
                    "continuity_refs": ["night_guard", "station_platform"],
                    "render": {"mode": "generate"},
                }
                for scene_id in range(1, 9)
            ],
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

    def _draft(self, *, project_id: str = "wizard-v2-smoke") -> tuple[dict, SequencedFakeProvider]:
        provider = SequencedFakeProvider([self._story(), self._visual()])
        result = WizardV2(provider).draft(
            "Strażnik odkrywa peron, którego nie ma w rozkładzie.",
            project_id=project_id,
        ).to_dict()
        return result, provider

    def assert_lossless_split(self, narration: str) -> list[str]:
        result = split_narration_into_scenes(narration)
        self.assertEqual(len(result), 8)
        self.assertTrue(all(result))
        self.assertEqual(normalize_whitespace(" ".join(result)), normalize_whitespace(narration))
        self.assertEqual(
            normalize_whitespace(" ".join(result)).split(),
            normalize_whitespace(narration).split(),
        )
        return result

    def test_splitter_returns_eight_scenes_for_eighty_words_many_sentences(self) -> None:
        result = self.assert_lossless_split(self._narration(80, sentence_every=10))
        self.assertEqual([len(scene.split()) for scene in result], [10] * 8)

    def test_splitter_returns_eight_scenes_for_one_long_sentence(self) -> None:
        result = self.assert_lossless_split(self._narration(80, sentence_every=None) + ".")
        self.assertEqual([len(scene.split()) for scene in result], [10] * 8)

    def test_splitter_supports_seventy_and_one_hundred_sixty_words(self) -> None:
        for word_count in (70, 160):
            with self.subTest(word_count=word_count):
                result = self.assert_lossless_split(self._narration(word_count, sentence_every=17))
                self.assertEqual(sum(len(scene.split()) for scene in result), word_count)

    def test_splitter_preserves_order_and_adds_or_removes_no_words(self) -> None:
        narration = "  " + self._narration(93, sentence_every=11).replace(" ", "  \n", 4) + "  "
        result = self.assert_lossless_split(narration)
        original_words = normalize_whitespace(narration).split()
        result_words = normalize_whitespace(" ".join(result)).split()
        self.assertEqual(result_words, original_words)
        self.assertEqual(len(result_words), len(original_words))

    def test_valid_story_and_visual_stages_build_compatible_draft(self) -> None:
        result, provider = self._draft()

        self.assertEqual(provider.calls, 2)
        self.assertEqual(result["draft"]["id"], "wizard-v2-smoke")
        self.assertTrue(result["draft"]["fictional"])
        self.assertEqual(len(result["draft"]["scenes"]), 8)
        self.assertEqual(result["shot_audit"]["score"], 100)
        self.assertTrue(result["shot_audit"]["ok"])
        self.assertEqual(result["provider"]["name"], "fake")
        self.assertEqual(result["provider"]["story_repairs"], 0)
        self.assertEqual(result["provider"]["visual_repairs"], 0)
        self.assertEqual(result["provider"]["repairs"], 0)
        self.assertEqual(result["provider"]["story_usage"]["total_tokens"], 15)
        self.assertEqual(result["provider"]["visual_usage"]["total_tokens"], 15)
        self.assertEqual(result["provider"]["usage"]["total_tokens"], 30)
        self.assertEqual(
            result["draft"]["narration"],
            " ".join(scene["text"] for scene in result["draft"]["scenes"]),
        )
        self.assertEqual(len(result["draft"]["narration"].split()), 100)
        self.assertNotIn(
            "visual_bible",
            provider.call_args[0]["messages"][1]["content"].lower(),
        )

    def test_short_story_is_repaired_once_before_visual_stage(self) -> None:
        provider = SequencedFakeProvider([self._story(19), self._story(100), self._visual()])

        result = WizardV2(provider).draft("Fikcyjny test naprawy story.").to_dict()

        self.assertEqual(provider.calls, 3)
        self.assertEqual(result["provider"]["story_repairs"], 1)
        self.assertEqual(result["provider"]["visual_repairs"], 0)
        self.assertEqual(result["provider"]["repairs"], 1)
        self.assertEqual(len(result["draft"]["narration"].split()), 100)

    def test_story_fails_after_exactly_one_unsuccessful_repair(self) -> None:
        provider = SequencedFakeProvider([self._story(19), self._story(20), self._visual()])

        with self.assertRaisesRegex(
            WizardV2Error,
            r"AI story invalid after one repair attempt: narration must contain 70-160 words, got 20",
        ):
            WizardV2(provider).draft("Fikcyjny test nieskutecznej naprawy story.")

        self.assertEqual(provider.calls, 2)

    def test_visual_stage_cannot_change_canonical_scene_text(self) -> None:
        visual = self._visual()
        for scene in visual["scenes"]:
            scene["text"] = "ZMIENIONY TEKST"
        story = self._story(100)
        expected = split_narration_into_scenes(story["narration"])
        provider = SequencedFakeProvider([story, visual])

        result = WizardV2(provider).draft("Fikcyjny test immutable scen.").to_dict()

        self.assertEqual([scene["text"] for scene in result["draft"]["scenes"]], expected)
        self.assertNotIn("ZMIENIONY TEKST", result["draft"]["narration"])

    def test_visual_continuity_mismatch_is_repaired_without_changing_story(self) -> None:
        story = self._story(100)
        invalid = self._visual()
        invalid["visual_bible"]["assignments"]["1"] = ["night_guard"]
        valid = self._visual()
        expected_scenes = split_narration_into_scenes(story["narration"])
        provider = SequencedFakeProvider([story, invalid, valid])

        result = WizardV2(provider).draft("Fikcyjny test naprawy Visual Bible.").to_dict()

        self.assertEqual(provider.calls, 3)
        self.assertEqual(result["provider"]["story_repairs"], 0)
        self.assertEqual(result["provider"]["visual_repairs"], 1)
        self.assertEqual(result["provider"]["repairs"], 1)
        self.assertEqual(result["draft"]["narration"], story["narration"])
        self.assertEqual([scene["text"] for scene in result["draft"]["scenes"]], expected_scenes)

    def test_missing_continuity_entity_is_rejected_after_one_visual_repair(self) -> None:
        invalid = self._visual()
        invalid["scenes"][0]["continuity_refs"] = ["night_guard", "flashlight"]
        invalid["visual_bible"]["assignments"]["1"] = ["night_guard"]
        provider = SequencedFakeProvider([self._story(), invalid, invalid])

        with self.assertRaisesRegex(WizardV2Error, r"undefined Visual Bible entities: flashlight"):
            WizardV2(provider).draft("Fikcyjny test brakującej encji.")
        self.assertEqual(provider.calls, 3)

    def test_global_continuity_ref_is_rejected(self) -> None:
        invalid = self._visual()
        invalid["scenes"][0]["continuity_refs"] = ["global_style"]
        invalid["visual_bible"]["assignments"]["1"] = []
        provider = SequencedFakeProvider([self._story(), invalid, invalid])

        with self.assertRaisesRegex(WizardV2Error, r"references global Visual Bible entities: global_style"):
            WizardV2(provider).draft("Fikcyjny test globalnego ref.")

    def test_global_visual_bible_assignment_is_rejected(self) -> None:
        invalid = self._visual()
        invalid["scenes"][0]["continuity_refs"] = []
        invalid["visual_bible"]["assignments"]["1"] = ["global_style"]
        provider = SequencedFakeProvider([self._story(), invalid, invalid])

        with self.assertRaisesRegex(WizardV2Error, r"assigns global Visual Bible entities: global_style"):
            WizardV2(provider).draft("Fikcyjny test globalnego assignment.")

    def test_duplicate_continuity_ref_is_rejected(self) -> None:
        invalid = self._visual()
        invalid["scenes"][0]["continuity_refs"] = ["night_guard", "night_guard"]
        invalid["visual_bible"]["assignments"]["1"] = ["night_guard"]
        provider = SequencedFakeProvider([self._story(), invalid, invalid])

        with self.assertRaisesRegex(WizardV2Error, r"duplicate continuity_refs: night_guard"):
            WizardV2(provider).draft("Fikcyjny test duplikatu ref.")

    def test_invalid_visual_scene_count_is_repaired_once(self) -> None:
        invalid = self._visual()
        invalid["scenes"] = invalid["scenes"][:7]
        provider = SequencedFakeProvider([self._story(), invalid, self._visual()])

        result = WizardV2(provider).draft(
            "Strażnik odkrywa peron, którego nie ma w rozkładzie.",
            project_id="wizard-v2-repair",
        ).to_dict()

        self.assertEqual(provider.calls, 3)
        self.assertEqual(len(result["draft"]["scenes"]), 8)
        self.assertEqual(result["provider"]["visual_repairs"], 1)

    def test_both_stages_receive_timeout_retry_and_stage_temperatures(self) -> None:
        provider = SequencedFakeProvider([self._story(), self._visual()])

        WizardV2(provider).draft("Fikcyjny test parametrów Wizarda V2.")

        self.assertEqual([call["request_timeout"] for call in provider.call_args], [180.0, 180.0])
        self.assertEqual([call["retries"] for call in provider.call_args], [1, 1])
        self.assertEqual([call["temperature"] for call in provider.call_args], [0.3, 0.2])
        self.assertEqual([call["max_tokens"] for call in provider.call_args], [1200, 3800])

    def test_reviewed_create_persists_project_and_visual_bible(self) -> None:
        draft, _provider = self._draft(project_id="wizard-v2-create")

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

    def test_reviewed_scene_edit_recalculates_persisted_narration(self) -> None:
        draft, _provider = self._draft(project_id="wizard-v2-reviewed-edit")
        draft["draft"]["scenes"][0]["text"] += " dodatkowe slowa po recenzji"
        draft["draft"]["narration"] = "nieaktualna narracja po ręcznej edycji"
        expected = " ".join(scene["text"] for scene in draft["draft"]["scenes"])

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with StudioStore(root / "studio.db") as store:
                result = create_reviewed_wizard_v2(
                    store,
                    shorts_dir=root / "shorts",
                    envelope=draft,
                )
                stored_narration = store.conn.execute(
                    "SELECT narration FROM projects WHERE project_id = ?",
                    ("wizard-v2-reviewed-edit",),
                ).fetchone()[0]

        self.assertEqual(result["project"]["narration"], expected)
        self.assertEqual(stored_narration, expected)

    def test_reviewed_create_rejects_missing_continuity_entity_without_writes(self) -> None:
        envelope, _provider = self._draft(project_id="wizard-v2-create-reject")
        envelope = deepcopy(envelope)
        envelope["draft"]["scenes"][0]["continuity_refs"].append("flashlight")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shorts = root / "shorts"
            db = root / "studio.db"
            with StudioStore(db) as store:
                with self.assertRaisesRegex(WizardV2Error, r"undefined Visual Bible entities: flashlight"):
                    create_reviewed_wizard_v2(store, shorts_dir=shorts, envelope=envelope)
                project_count = store.conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
                self.assertEqual(project_count, 0)

            self.assertFalse((shorts / "wizard-v2-create-reject.yaml").exists())


if __name__ == "__main__":
    unittest.main()
