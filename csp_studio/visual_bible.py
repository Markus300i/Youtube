from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .models import Scene, utc_now
from .store import StudioStore

VISUAL_BIBLE_SCHEMA = """
CREATE TABLE IF NOT EXISTS visual_bible_entities (
    project_id TEXT NOT NULL,
    entity_key TEXT NOT NULL,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    prompt_fragment TEXT NOT NULL DEFAULT '',
    reference_asset_path TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    active INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (project_id, entity_key),
    FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS visual_bible_scene_refs (
    project_id TEXT NOT NULL,
    scene_id INTEGER NOT NULL,
    entity_key TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (project_id, scene_id, entity_key),
    FOREIGN KEY (project_id, scene_id) REFERENCES scenes(project_id, scene_id) ON DELETE CASCADE,
    FOREIGN KEY (project_id, entity_key) REFERENCES visual_bible_entities(project_id, entity_key) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_visual_bible_kind
ON visual_bible_entities(project_id, kind, active);
"""

VALID_KINDS = {"style", "character", "location", "object", "wardrobe", "vehicle", "lighting", "rule"}


@dataclass(slots=True)
class VisualBibleEntity:
    project_id: str
    entity_key: str
    kind: str
    name: str
    description: str = ""
    prompt_fragment: str = ""
    reference_asset_path: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    active: bool = True
    updated_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "entity_key": self.entity_key,
            "kind": self.kind,
            "name": self.name,
            "description": self.description,
            "prompt_fragment": self.prompt_fragment,
            "reference_asset_path": self.reference_asset_path,
            "metadata": dict(self.metadata),
            "active": self.active,
            "updated_at": self.updated_at,
        }


class VisualBible:
    """Canonical visual continuity entities for one CSP project.

    The Bible stores stable descriptions/references independently from scene
    prompts. Scenes explicitly reference entities; prompt compilation is derived
    state and can be regenerated without losing the canonical Bible.
    """

    def __init__(self, store: StudioStore):
        self.store = store
        self.store.conn.executescript(VISUAL_BIBLE_SCHEMA)
        self.store.conn.commit()

    def upsert(self, entity: VisualBibleEntity) -> VisualBibleEntity:
        if entity.kind not in VALID_KINDS:
            raise ValueError(f"Unsupported Visual Bible kind: {entity.kind}")
        if not entity.entity_key.strip():
            raise ValueError("entity_key is required")
        if not entity.name.strip():
            raise ValueError("name is required")
        if self.store.conn.execute("SELECT 1 FROM projects WHERE project_id=?", (entity.project_id,)).fetchone() is None:
            raise KeyError(f"Unknown project: {entity.project_id}")
        now = utc_now()
        path = str(Path(entity.reference_asset_path).expanduser().resolve()) if entity.reference_asset_path else None
        self.store.conn.execute(
            """
            INSERT INTO visual_bible_entities(
                project_id,entity_key,kind,name,description,prompt_fragment,
                reference_asset_path,metadata_json,active,updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(project_id,entity_key) DO UPDATE SET
                kind=excluded.kind,
                name=excluded.name,
                description=excluded.description,
                prompt_fragment=excluded.prompt_fragment,
                reference_asset_path=excluded.reference_asset_path,
                metadata_json=excluded.metadata_json,
                active=excluded.active,
                updated_at=excluded.updated_at
            """,
            (
                entity.project_id,
                entity.entity_key,
                entity.kind,
                entity.name,
                entity.description,
                entity.prompt_fragment,
                path,
                json.dumps(entity.metadata, ensure_ascii=False),
                int(entity.active),
                now,
            ),
        )
        self.store.conn.commit()
        return self.get(entity.project_id, entity.entity_key)  # type: ignore[return-value]

    def get(self, project_id: str, entity_key: str) -> VisualBibleEntity | None:
        row = self.store.conn.execute(
            "SELECT * FROM visual_bible_entities WHERE project_id=? AND entity_key=?",
            (project_id, entity_key),
        ).fetchone()
        return self._row(row) if row else None

    def list(self, project_id: str, *, kind: str | None = None, active_only: bool = True) -> list[VisualBibleEntity]:
        where = ["project_id=?"]
        params: list[Any] = [project_id]
        if kind is not None:
            if kind not in VALID_KINDS:
                raise ValueError(f"Unsupported Visual Bible kind: {kind}")
            where.append("kind=?")
            params.append(kind)
        if active_only:
            where.append("active=1")
        rows = self.store.conn.execute(
            f"SELECT * FROM visual_bible_entities WHERE {' AND '.join(where)} ORDER BY kind,entity_key",
            tuple(params),
        ).fetchall()
        return [self._row(row) for row in rows]

    def assign(self, project_id: str, scene_id: int, entity_keys: list[str]) -> list[str]:
        scene = self.store.get_scene(project_id, scene_id)
        if scene is None:
            raise KeyError(f"Unknown scene: {project_id}:{scene_id}")
        unique = list(dict.fromkeys(key.strip() for key in entity_keys if key.strip()))
        for key in unique:
            entity = self.get(project_id, key)
            if entity is None or not entity.active:
                raise KeyError(f"Unknown active Visual Bible entity: {project_id}:{key}")
        self.store.conn.execute(
            "DELETE FROM visual_bible_scene_refs WHERE project_id=? AND scene_id=?",
            (project_id, scene_id),
        )
        now = utc_now()
        self.store.conn.executemany(
            "INSERT INTO visual_bible_scene_refs(project_id,scene_id,entity_key,updated_at) VALUES (?,?,?,?)",
            [(project_id, scene_id, key, now) for key in unique],
        )
        self.store.conn.commit()
        return unique

    def assigned(self, project_id: str, scene_id: int) -> list[VisualBibleEntity]:
        rows = self.store.conn.execute(
            """
            SELECT e.* FROM visual_bible_entities e
            JOIN visual_bible_scene_refs r
              ON r.project_id=e.project_id AND r.entity_key=e.entity_key
            WHERE r.project_id=? AND r.scene_id=? AND e.active=1
            ORDER BY e.kind,e.entity_key
            """,
            (project_id, scene_id),
        ).fetchall()
        return [self._row(row) for row in rows]

    def sync_scene_continuity_refs(self, scene: Scene) -> list[str]:
        known = {item.entity_key for item in self.list(scene.project_id)}
        keys = [key for key in scene.continuity_refs if key in known]
        return self.assign(scene.project_id, scene.scene_id, keys)

    def prompt_context(self, project_id: str, scene_id: int) -> str:
        globals_ = self.list(project_id, kind="style") + self.list(project_id, kind="rule")
        assigned = self.assigned(project_id, scene_id)
        ordered: list[VisualBibleEntity] = []
        seen: set[str] = set()
        for item in globals_ + assigned:
            if item.entity_key in seen:
                continue
            seen.add(item.entity_key)
            ordered.append(item)
        fragments = [item.prompt_fragment.strip() or item.description.strip() for item in ordered]
        return "; ".join(fragment for fragment in fragments if fragment)

    def compile_prompt(self, scene: Scene) -> str:
        context = self.prompt_context(scene.project_id, scene.scene_id)
        return f"{context}. {scene.prompt}" if context else scene.prompt

    @staticmethod
    def _row(row) -> VisualBibleEntity:
        return VisualBibleEntity(
            project_id=row["project_id"],
            entity_key=row["entity_key"],
            kind=row["kind"],
            name=row["name"],
            description=row["description"],
            prompt_fragment=row["prompt_fragment"],
            reference_asset_path=row["reference_asset_path"],
            metadata=json.loads(row["metadata_json"] or "{}"),
            active=bool(row["active"]),
            updated_at=row["updated_at"],
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect CSP Visual Bible V2")
    parser.add_argument("--db", required=True)
    parser.add_argument("project_id")
    parser.add_argument("--scene-id", type=int)
    args = parser.parse_args()
    with StudioStore(args.db) as store:
        bible = VisualBible(store)
        if args.scene_id:
            payload = {
                "entities": [item.to_dict() for item in bible.assigned(args.project_id, args.scene_id)],
                "prompt_context": bible.prompt_context(args.project_id, args.scene_id),
            }
        else:
            payload = [item.to_dict() for item in bible.list(args.project_id, active_only=False)]
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
