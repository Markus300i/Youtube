from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from csp_studio.agent_memory import AgentOneMemoryAdvisor
from csp_studio.import_short import project_from_short
from csp_studio.providers.base import ProviderResponse
from csp_studio.store import StudioStore


class FakeMemoryProvider:
    name = "fake_memory"
    embed_model = "fake-embed"

    def __init__(self):
        self.chat_messages = None

    def embed(self, texts, *, model=None, input_type="passage"):
        vectors = []
        for text in texts:
            lowered = text.lower()
            vectors.append([
                float(lowered.count("drzwi") + lowered.count("door")),
                float(lowered.count("piwn") + lowered.count("basement")),
                float(lowered.count("las") + lowered.count("forest")),
            ])
        return vectors

    def chat(self, messages, *, model=None, temperature=0.2, max_tokens=2048):
        self.chat_messages = messages
        return ProviderResponse(
            provider=self.name,
            model="fake-chat",
            text="Stan bez zmian. Pamięć wskazuje podobny motyw drzwi.",
        )


class AgentOneMemoryAdvisorTests(unittest.TestCase):
    def _project(self, project_id: str, title: str, narration: str):
        return project_from_short(
            {
                "id": project_id,
                "title": title,
                "narration": narration,
                "scenes": [
                    {
                        "id": index,
                        "text": f"{narration} scena {index}",
                        "prompt": f"Prompt {index}",
                        "motion": "static",
                    }
                    for index in range(1, 9)
                ],
            }
        )

    def test_recall_excludes_current_project_by_default(self):
        provider = FakeMemoryProvider()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with StudioStore(root / "studio.db") as store:
                store.upsert_project(self._project("001", "Drzwi 0", "Drzwi w piwnicy"))
                store.upsert_project(self._project("002", "Drzwi pod schodami", "Stare drzwi prowadzą do piwnicy"))
                advisor = AgentOneMemoryAdvisor(store, output_root=root)
                advisor.memory.ingest_project_summary("001")
                advisor.memory.ingest_project_summary("002")
                advisor.memory.embed_pending(provider)

                matches = advisor.recall("001", provider, top_k=5)
                self.assertTrue(matches)
                self.assertTrue(all(item["source_project_id"] != "001" for item in matches))
                self.assertTrue(any(item["source_project_id"] == "002" for item in matches))

    def test_advise_keeps_deterministic_next_action(self):
        provider = FakeMemoryProvider()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with StudioStore(root / "studio.db") as store:
                store.upsert_project(self._project("001", "Drzwi 0", "Drzwi w piwnicy"))
                store.upsert_project(self._project("002", "Drzwi pod schodami", "Stare drzwi prowadzą do piwnicy"))
                advisor = AgentOneMemoryAdvisor(store, output_root=root)
                advisor.memory.ingest_project_summary("001")
                advisor.memory.ingest_project_summary("002")

                result = advisor.advise("001", provider, memory_provider=provider, top_k=3)
                self.assertEqual(result["report"]["next_action"], "complete_images")
                self.assertFalse(result["report"]["final_ready"])
                self.assertTrue(result["memory"]["matches"])
                self.assertIsNone(result["memory"]["error"])
                self.assertIn("complete_images", str(provider.chat_messages))

    def test_memory_failure_does_not_change_readiness_or_block_advice(self):
        class BrokenEmbeddingProvider(FakeMemoryProvider):
            def embed(self, texts, *, model=None, input_type="passage"):
                raise RuntimeError("embedding unavailable")

        provider = BrokenEmbeddingProvider()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with StudioStore(root / "studio.db") as store:
                store.upsert_project(self._project("001", "Drzwi 0", "Drzwi w piwnicy"))
                advisor = AgentOneMemoryAdvisor(store, output_root=root)
                result = advisor.advise("001", provider, memory_provider=provider)
                self.assertEqual(result["report"]["next_action"], "complete_images")
                self.assertEqual(result["memory"]["matches"], [])
                self.assertIn("embedding unavailable", result["memory"]["error"])
                self.assertIn("Stan bez zmian", result["assistant"]["text"])


if __name__ == "__main__":
    unittest.main()
