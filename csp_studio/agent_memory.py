from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from .agent_one import AgentOne, DB_PATH, OUTPUT_ROOT
from .providers import get_provider
from .providers.base import ChatProvider, EmbeddingProvider
from .store import StudioStore
from .universe_memory import UniverseMemory


class AgentOneMemoryAdvisor:
    """Optional semantic-memory layer for Agent One.

    Deterministic readiness remains owned by AgentOne.inspect(). Memory results may
    enrich recommendations, but they never change readiness gates or next_action.
    """

    def __init__(self, store: StudioStore, *, output_root: str | Path | None = None):
        self.store = store
        self.agent = AgentOne(store, output_root=output_root or OUTPUT_ROOT)
        self.memory = UniverseMemory(store)

    def _project_query(self, project_id: str) -> str:
        row = self.store.conn.execute("SELECT * FROM projects WHERE project_id=?", (project_id,)).fetchone()
        if row is None:
            raise KeyError(f"Unknown project: {project_id}")
        scenes = self.store.list_scenes(project_id)
        scene_text = " ".join(scene.text for scene in scenes)
        parts = [
            str(row["title"] or "").strip(),
            str(row["series"] or "").strip(),
            str(row["narration"] or "").strip(),
            str(row["visual_style"] or "").strip(),
            scene_text.strip(),
        ]
        return "\n".join(part for part in parts if part)

    def recall(
        self,
        project_id: str,
        provider: EmbeddingProvider,
        *,
        top_k: int = 5,
        include_current: bool = False,
    ) -> list[dict[str, Any]]:
        query = self._project_query(project_id)
        requested = max(1, int(top_k))
        matches = self.memory.search(query, provider, top_k=max(requested * 4, requested))
        output: list[dict[str, Any]] = []
        for match in matches:
            if not include_current and match.item.source_project_id == project_id:
                continue
            output.append(
                {
                    "score": match.score,
                    "memory_id": match.item.memory_id,
                    "memory_key": match.item.memory_key,
                    "kind": match.item.kind,
                    "text": match.item.text,
                    "source_project_id": match.item.source_project_id,
                    "metadata": match.item.metadata,
                    "provider": match.provider,
                    "model": match.model,
                }
            )
            if len(output) >= requested:
                break
        return output

    def advise(
        self,
        project_id: str,
        provider: ChatProvider,
        *,
        memory_provider: EmbeddingProvider | None = None,
        top_k: int = 5,
    ) -> dict[str, Any]:
        report = self.agent.inspect(project_id)
        embedding_provider = memory_provider or provider
        recall: list[dict[str, Any]] = []
        recall_error: str | None = None
        embed = getattr(embedding_provider, "embed", None)
        if callable(embed):
            try:
                recall = self.recall(project_id, embedding_provider, top_k=top_k)
            except Exception as exc:
                recall_error = f"{type(exc).__name__}: {exc}"

        safe_state = report.to_dict()
        memory_context = {
            "matches": recall,
            "error": recall_error,
            "rule": "Memory is advisory only and cannot change deterministic readiness or next_action.",
        }
        prompt = (
            "Jesteś Agent One dla fikcyjnego kanału Ciemna Strona Polski. "
            "Najpierw respektuj deterministyczny stan produkcji. Nie wolno Ci zmieniać readiness, stage ani next_action. "
            "Pamięć uniwersum jest wyłącznie kontekstem doradczym: użyj jej do wskazania podobnych motywów, ryzyka powtórzeń "
            "lub okazji do continuity. Jeśli pamięć jest pusta, powiedz krótko, że brak porównania z wcześniejszymi projektami. "
            "Napisz krótki raport po polsku: stan, najbliższy krok, następnie 1-3 uwagi z pamięci.\n\n"
            "ZWERYFIKOWANY STAN:\n"
            + json.dumps(safe_state, ensure_ascii=False, indent=2)
            + "\n\nPAMIĘĆ UNIWERSUM:\n"
            + json.dumps(memory_context, ensure_ascii=False, indent=2)
        )
        response = provider.chat(
            [
                {
                    "role": "system",
                    "content": (
                        "Jesteś operatorem produkcyjnym CSP. Fakty dostarcza deterministyczny Agent One; "
                        "semantic memory może tylko wzbogacać rekomendacje."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            max_tokens=900,
        )
        return {
            "report": safe_state,
            "memory": memory_context,
            "assistant": {
                "provider": response.provider,
                "model": response.model,
                "text": response.text,
                "usage": response.usage,
            },
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="Agent One with optional CSP Universe Memory context")
    parser.add_argument("project_id")
    parser.add_argument("--provider", default=os.getenv("CSP_AI_PROVIDER", "nvidia_nim"))
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--include-current", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    with StudioStore(DB_PATH) as store:
        provider = get_provider(args.provider)
        try:
            advisor = AgentOneMemoryAdvisor(store, output_root=OUTPUT_ROOT)
            if args.include_current:
                report = advisor.agent.inspect(args.project_id)
                memories = advisor.recall(args.project_id, provider, top_k=args.top_k, include_current=True)
                result = {"report": report.to_dict(), "memory": {"matches": memories, "error": None}}
            else:
                result = advisor.advise(args.project_id, provider, memory_provider=provider, top_k=args.top_k)
        finally:
            close = getattr(provider, "close", None)
            if callable(close):
                close()

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    report = result["report"]
    print(f"AGENT ONE + MEMORY: {report['project_id']} — {report['title']}")
    print(f"STAGE: {report['stage']}")
    print(f"NEXT: {report['next_action']} — {report['next_action_detail']}")
    matches = result.get("memory", {}).get("matches") or []
    print(f"MEMORY MATCHES: {len(matches)}")
    for match in matches:
        print(f"{match['score']:.4f} {match['kind']:10s} {match['memory_key']}: {match['text'][:140]}")
    assistant = result.get("assistant")
    if assistant:
        print("\nADVISOR:")
        print(assistant["text"])


if __name__ == "__main__":
    main()
