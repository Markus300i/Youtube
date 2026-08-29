from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .models import utc_now
from .providers import EmbeddingProvider, get_provider
from .store import StudioStore

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_ROOT = Path(os.getenv("CSP_OUTPUT_DIR", str(ROOT / "output"))).expanduser().resolve()
DB_PATH = Path(os.getenv("CSP_STUDIO_DB", str(OUTPUT_ROOT / "csp-studio.db"))).expanduser().resolve()

MEMORY_SCHEMA = """
CREATE TABLE IF NOT EXISTS universe_memory (
    memory_id INTEGER PRIMARY KEY AUTOINCREMENT,
    namespace TEXT NOT NULL DEFAULT 'csp',
    memory_key TEXT NOT NULL,
    kind TEXT NOT NULL,
    text TEXT NOT NULL,
    source_project_id TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(namespace, memory_key)
);

CREATE INDEX IF NOT EXISTS idx_universe_memory_kind
ON universe_memory(namespace, kind, active);

CREATE TABLE IF NOT EXISTS universe_memory_embeddings (
    memory_id INTEGER NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    vector_json TEXT NOT NULL,
    dimensions INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (memory_id, provider, model),
    FOREIGN KEY (memory_id) REFERENCES universe_memory(memory_id) ON DELETE CASCADE
);
"""


@dataclass(slots=True)
class MemoryItem:
    memory_id: int
    namespace: str
    memory_key: str
    kind: str
    text: str
    source_project_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    active: bool = True
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class MemoryMatch:
    item: MemoryItem
    score: float
    provider: str
    model: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "provider": self.provider,
            "model": self.model,
            "item": self.item.to_dict(),
        }


class UniverseMemory:
    """Canonical CSP memory with derived provider embeddings.

    Text/metadata are canonical. Vectors are disposable derived state and may be
    rebuilt with a different provider/model/index without changing memory items.
    """

    def __init__(self, store: StudioStore):
        self.store = store
        self.store.conn.executescript(MEMORY_SCHEMA)
        self.store.conn.commit()

    @staticmethod
    def _hash(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def remember(
        self,
        memory_key: str,
        *,
        kind: str,
        text: str,
        namespace: str = "csp",
        source_project_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        active: bool = True,
    ) -> MemoryItem:
        key = memory_key.strip()
        kind = kind.strip()
        text = text.strip()
        namespace = namespace.strip() or "csp"
        if not key:
            raise ValueError("memory_key cannot be empty")
        if not kind:
            raise ValueError("kind cannot be empty")
        if not text:
            raise ValueError("text cannot be empty")
        if source_project_id is not None:
            exists = self.store.conn.execute(
                "SELECT 1 FROM projects WHERE project_id=?", (source_project_id,)
            ).fetchone()
            if exists is None:
                raise KeyError(f"Unknown source project: {source_project_id}")

        now = utc_now()
        existing = self.store.conn.execute(
            "SELECT memory_id,created_at FROM universe_memory WHERE namespace=? AND memory_key=?",
            (namespace, key),
        ).fetchone()
        if existing is None:
            cursor = self.store.conn.execute(
                """
                INSERT INTO universe_memory (
                    namespace,memory_key,kind,text,source_project_id,metadata_json,active,created_at,updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    namespace,
                    key,
                    kind,
                    text,
                    source_project_id,
                    json.dumps(metadata or {}, ensure_ascii=False),
                    int(active),
                    now,
                    now,
                ),
            )
            memory_id = int(cursor.lastrowid)
        else:
            memory_id = int(existing["memory_id"])
            self.store.conn.execute(
                """
                UPDATE universe_memory
                SET kind=?,text=?,source_project_id=?,metadata_json=?,active=?,updated_at=?
                WHERE memory_id=?
                """,
                (
                    kind,
                    text,
                    source_project_id,
                    json.dumps(metadata or {}, ensure_ascii=False),
                    int(active),
                    now,
                    memory_id,
                ),
            )
        self.store.conn.commit()
        item = self.get(memory_id)
        assert item is not None
        return item

    def get(self, memory_id: int) -> MemoryItem | None:
        row = self.store.conn.execute(
            "SELECT * FROM universe_memory WHERE memory_id=?", (memory_id,)
        ).fetchone()
        return self._row_to_item(row) if row else None

    def get_by_key(self, memory_key: str, *, namespace: str = "csp") -> MemoryItem | None:
        row = self.store.conn.execute(
            "SELECT * FROM universe_memory WHERE namespace=? AND memory_key=?",
            (namespace, memory_key),
        ).fetchone()
        return self._row_to_item(row) if row else None

    def list(self, *, namespace: str = "csp", kind: str | None = None, active_only: bool = True) -> list[MemoryItem]:
        where = ["namespace=?"]
        params: list[Any] = [namespace]
        if kind:
            where.append("kind=?")
            params.append(kind)
        if active_only:
            where.append("active=1")
        rows = self.store.conn.execute(
            f"SELECT * FROM universe_memory WHERE {' AND '.join(where)} ORDER BY updated_at DESC, memory_id DESC",
            tuple(params),
        ).fetchall()
        return [self._row_to_item(row) for row in rows]

    def deactivate(self, memory_id: int) -> MemoryItem:
        if self.get(memory_id) is None:
            raise KeyError(f"Unknown memory item: {memory_id}")
        now = utc_now()
        self.store.conn.execute(
            "UPDATE universe_memory SET active=0,updated_at=? WHERE memory_id=?",
            (now, memory_id),
        )
        self.store.conn.commit()
        item = self.get(memory_id)
        assert item is not None
        return item

    def embed_pending(
        self,
        provider: EmbeddingProvider,
        *,
        namespace: str = "csp",
        model: str | None = None,
        batch_size: int = 32,
    ) -> int:
        items = self.list(namespace=namespace, active_only=True)
        if not items:
            return 0
        provider_name = str(getattr(provider, "name", type(provider).__name__))
        selected_model = model or str(getattr(provider, "embed_model", "default"))
        pending: list[MemoryItem] = []
        for item in items:
            row = self.store.conn.execute(
                """
                SELECT content_hash FROM universe_memory_embeddings
                WHERE memory_id=? AND provider=? AND model=?
                """,
                (item.memory_id, provider_name, selected_model),
            ).fetchone()
            if row is None or row["content_hash"] != self._hash(item.text):
                pending.append(item)

        embedded = 0
        for offset in range(0, len(pending), max(1, batch_size)):
            batch = pending[offset : offset + max(1, batch_size)]
            vectors = provider.embed([item.text for item in batch], model=model, input_type="passage")
            if len(vectors) != len(batch):
                raise RuntimeError("Embedding provider returned a different number of vectors than inputs")
            now = utc_now()
            for item, vector in zip(batch, vectors):
                if not vector:
                    raise RuntimeError(f"Empty embedding for memory item {item.memory_id}")
                self.store.conn.execute(
                    """
                    INSERT INTO universe_memory_embeddings (
                        memory_id,provider,model,content_hash,vector_json,dimensions,updated_at
                    ) VALUES (?,?,?,?,?,?,?)
                    ON CONFLICT(memory_id,provider,model) DO UPDATE SET
                        content_hash=excluded.content_hash,
                        vector_json=excluded.vector_json,
                        dimensions=excluded.dimensions,
                        updated_at=excluded.updated_at
                    """,
                    (
                        item.memory_id,
                        provider_name,
                        selected_model,
                        self._hash(item.text),
                        json.dumps([float(value) for value in vector]),
                        len(vector),
                        now,
                    ),
                )
                embedded += 1
            self.store.conn.commit()
        return embedded

    def search(
        self,
        query: str,
        provider: EmbeddingProvider,
        *,
        namespace: str = "csp",
        model: str | None = None,
        top_k: int = 5,
        kind: str | None = None,
        auto_embed: bool = True,
    ) -> list[MemoryMatch]:
        query = query.strip()
        if not query:
            return []
        if auto_embed:
            self.embed_pending(provider, namespace=namespace, model=model)
        provider_name = str(getattr(provider, "name", type(provider).__name__))
        selected_model = model or str(getattr(provider, "embed_model", "default"))
        query_vectors = provider.embed([query], model=model, input_type="query")
        if not query_vectors or not query_vectors[0]:
            raise RuntimeError("Embedding provider returned no query vector")
        query_vector = [float(value) for value in query_vectors[0]]

        where = ["m.namespace=?", "m.active=1", "e.provider=?", "e.model=?"]
        params: list[Any] = [namespace, provider_name, selected_model]
        if kind:
            where.append("m.kind=?")
            params.append(kind)
        rows = self.store.conn.execute(
            f"""
            SELECT m.*,e.vector_json,e.dimensions
            FROM universe_memory m
            JOIN universe_memory_embeddings e ON e.memory_id=m.memory_id
            WHERE {' AND '.join(where)}
            """,
            tuple(params),
        ).fetchall()

        matches: list[MemoryMatch] = []
        for row in rows:
            vector = [float(value) for value in json.loads(row["vector_json"])]
            if len(vector) != len(query_vector):
                continue
            score = self._cosine(query_vector, vector)
            matches.append(
                MemoryMatch(
                    item=self._row_to_item(row),
                    score=round(score, 6),
                    provider=provider_name,
                    model=selected_model,
                )
            )
        matches.sort(key=lambda item: item.score, reverse=True)
        return matches[: max(1, int(top_k))]

    def ingest_project_summary(self, project_id: str, *, include_scenes: bool = True) -> list[MemoryItem]:
        row = self.store.conn.execute("SELECT * FROM projects WHERE project_id=?", (project_id,)).fetchone()
        if row is None:
            raise KeyError(f"Unknown project: {project_id}")
        created = [
            self.remember(
                f"project:{project_id}",
                kind="story",
                text=(
                    f"Projekt {row['title']} z serii {row['series'] or 'bez serii'}. "
                    f"Narracja: {row['narration']} Visual style: {row['visual_style']}"
                ).strip(),
                source_project_id=project_id,
                metadata={"title": row["title"], "series": row["series"]},
            )
        ]
        if include_scenes:
            for scene in self.store.list_scenes(project_id):
                created.append(
                    self.remember(
                        f"project:{project_id}:scene:{scene.scene_id:02d}",
                        kind="scene",
                        text=(
                            f"{row['title']} scena {scene.scene_id}: {scene.text} "
                            f"Shot: {scene.shot.shot_type}, camera: {scene.shot.camera}, purpose: {scene.shot.purpose}. "
                            f"Continuity: {', '.join(scene.continuity_refs)}. Prompt: {scene.prompt}"
                        ).strip(),
                        source_project_id=project_id,
                        metadata={
                            "scene_id": scene.scene_id,
                            "status": scene.status,
                            "continuity_refs": scene.continuity_refs,
                        },
                    )
                )
        return created

    @staticmethod
    def _cosine(left: list[float], right: list[float]) -> float:
        dot = sum(a * b for a, b in zip(left, right))
        left_norm = math.sqrt(sum(value * value for value in left))
        right_norm = math.sqrt(sum(value * value for value in right))
        if left_norm == 0 or right_norm == 0:
            return 0.0
        return dot / (left_norm * right_norm)

    @staticmethod
    def _row_to_item(row) -> MemoryItem:
        return MemoryItem(
            memory_id=int(row["memory_id"]),
            namespace=row["namespace"],
            memory_key=row["memory_key"],
            kind=row["kind"],
            text=row["text"],
            source_project_id=row["source_project_id"],
            metadata=json.loads(row["metadata_json"] or "{}"),
            active=bool(row["active"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="CSP Universe Memory")
    sub = parser.add_subparsers(dest="command", required=True)

    ingest = sub.add_parser("ingest-project")
    ingest.add_argument("project_id")

    embed = sub.add_parser("embed")
    embed.add_argument("--provider", default=os.getenv("CSP_AI_PROVIDER", "nvidia_nim"))

    search = sub.add_parser("search")
    search.add_argument("query")
    search.add_argument("--provider", default=os.getenv("CSP_AI_PROVIDER", "nvidia_nim"))
    search.add_argument("--top-k", type=int, default=5)
    search.add_argument("--kind")

    args = parser.parse_args()
    with StudioStore(DB_PATH) as store:
        memory = UniverseMemory(store)
        if args.command == "ingest-project":
            items = memory.ingest_project_summary(args.project_id)
            print(f"MEMORY ITEMS: {len(items)}")
            return

        provider = get_provider(args.provider)
        try:
            if args.command == "embed":
                count = memory.embed_pending(provider)
                print(f"EMBEDDED: {count}")
            elif args.command == "search":
                matches = memory.search(args.query, provider, top_k=args.top_k, kind=args.kind)
                for match in matches:
                    print(f"{match.score:.4f} {match.item.kind:10s} {match.item.memory_key}: {match.item.text[:160]}")
        finally:
            close = getattr(provider, "close", None)
            if callable(close):
                close()


if __name__ == "__main__":
    main()
