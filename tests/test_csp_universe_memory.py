from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from csp_studio.import_short import project_from_short
from csp_studio.store import StudioStore
from csp_studio.universe_memory import UniverseMemory


class FakeEmbeddingProvider:
    name = "fake_embed"
    embed_model = "fake-v1"

    def __init__(self):
        self.calls = []

    def embed(self, texts, *, model=None, input_type="passage"):
        self.calls.append((list(texts), input_type))
        vectors = []
        for raw in texts:
            text = raw.lower()
            door = text.count("door") + text.count("drzwi")
            forest = text.count("forest") + text.count("las")
            other = max(1, len(text.split())) / 100.0
            vectors.append([float(door), float(forest), float(other)])
        return vectors


class UniverseMemoryTests(unittest.TestCase):
    def test_memory_upsert_keeps_identity_and_reembeds_changed_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            with StudioStore(Path(tmp) / "studio.db") as store:
                memory = UniverseMemory(store)
                provider = FakeEmbeddingProvider()
                first = memory.remember("place:door", kind="location", text="Old mysterious door")
                self.assertEqual(memory.embed_pending(provider), 1)
                self.assertEqual(memory.embed_pending(provider), 0)

                updated = memory.remember("place:door", kind="location", text="Old mysterious door in basement")
                self.assertEqual(updated.memory_id, first.memory_id)
                self.assertEqual(memory.embed_pending(provider), 1)
                self.assertEqual(len(memory.list()), 1)

    def test_semantic_search_uses_query_embedding_and_ranks_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            with StudioStore(Path(tmp) / "studio.db") as store:
                memory = UniverseMemory(store)
                provider = FakeEmbeddingProvider()
                memory.remember("door", kind="object", text="Dark basement door with metal handle")
                memory.remember("forest", kind="location", text="Silent forest path at night")
                memory.embed_pending(provider)

                matches = memory.search("mysterious door", provider, top_k=2, auto_embed=False)
                self.assertEqual(matches[0].item.memory_key, "door")
                self.assertGreater(matches[0].score, matches[1].score)
                self.assertEqual(provider.calls[-1][1], "query")

    def test_inactive_memory_is_not_returned(self):
        with tempfile.TemporaryDirectory() as tmp:
            with StudioStore(Path(tmp) / "studio.db") as store:
                memory = UniverseMemory(store)
                provider = FakeEmbeddingProvider()
                item = memory.remember("door", kind="object", text="Door in basement")
                memory.embed_pending(provider)
                memory.deactivate(item.memory_id)
                self.assertEqual(memory.search("door", provider, auto_embed=False), [])

    def test_project_ingest_creates_story_and_scene_memories(self):
        with tempfile.TemporaryDirectory() as tmp:
            with StudioStore(Path(tmp) / "studio.db") as store:
                project = project_from_short(
                    {
                        "id": "001",
                        "title": "Drzwi 0",
                        "series": "Nie Otwieraj",
                        "narration": "Historia tajemniczych drzwi.",
                        "visual_style": "Polish documentary thriller",
                        "scenes": [
                            {"id": index, "text": f"Scena {index} drzwi", "prompt": f"Prompt {index}"}
                            for index in range(1, 9)
                        ],
                    }
                )
                store.upsert_project(project)
                memory = UniverseMemory(store)
                created = memory.ingest_project_summary("001")
                self.assertEqual(len(created), 9)
                self.assertEqual(memory.get_by_key("project:001").kind, "story")
                scene = memory.get_by_key("project:001:scene:03")
                self.assertEqual(scene.metadata["scene_id"], 3)
                self.assertEqual(scene.source_project_id, "001")


if __name__ == "__main__":
    unittest.main()
