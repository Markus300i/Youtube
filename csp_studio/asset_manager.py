from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import Asset, utc_now
from .store import StudioStore


ASSET_SCHEMA = """
CREATE TABLE IF NOT EXISTS assets (
    asset_id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    scene_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    path TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'candidate',
    revision INTEGER NOT NULL DEFAULT 1,
    active INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL DEFAULT 'manual',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY (project_id, scene_id) REFERENCES scenes(project_id, scene_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_assets_scene
ON assets(project_id, scene_id, kind, revision DESC);
"""


VALID_SCENE_STATUSES = {
    "draft",
    "generated",
    "approved",
    "needs_regeneration",
    "render_ready",
}


class AssetManager:
    def __init__(self, store: StudioStore):
        self.store = store
        self.store.conn.executescript(ASSET_SCHEMA)
        self.store.conn.commit()

    def next_revision(self, project_id: str, scene_id: int, kind: str = "image") -> int:
        row = self.store.conn.execute(
            """
            SELECT COALESCE(MAX(revision), 0) AS max_revision
            FROM assets WHERE project_id=? AND scene_id=? AND kind=?
            """,
            (project_id, scene_id, kind),
        ).fetchone()
        return int(row["max_revision"]) + 1

    def register_asset(
        self,
        project_id: str,
        scene_id: int,
        path: str | Path,
        *,
        kind: str = "image",
        source: str = "manual",
        status: str = "candidate",
        metadata: dict[str, Any] | None = None,
        activate: bool = True,
    ) -> Asset:
        scene = self.store.get_scene(project_id, scene_id)
        if scene is None:
            raise KeyError(f"Unknown scene {project_id}:{scene_id}")

        path_str = str(Path(path).expanduser().resolve())
        revision = self.next_revision(project_id, scene_id, kind)

        if activate:
            self.store.conn.execute(
                "UPDATE assets SET active=0 WHERE project_id=? AND scene_id=? AND kind=?",
                (project_id, scene_id, kind),
            )

        now = utc_now()
        cursor = self.store.conn.execute(
            """
            INSERT INTO assets (
                project_id,scene_id,kind,path,status,revision,active,source,metadata_json,created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (
                project_id,
                scene_id,
                kind,
                path_str,
                status,
                revision,
                int(activate),
                source,
                json.dumps(metadata or {}, ensure_ascii=False),
                now,
            ),
        )
        asset_id = int(cursor.lastrowid)

        if activate:
            scene.asset_path = path_str
            scene.status = "generated" if status == "candidate" else status
            self.store.upsert_scene(
                scene,
                action="replace_asset" if revision > 1 else "attach_asset",
                note=f"{kind} r{revision}: {path_str}",
            )
        else:
            self.store.conn.commit()

        return Asset(
            asset_id=asset_id,
            project_id=project_id,
            scene_id=scene_id,
            kind=kind,
            path=path_str,
            status=status,
            revision=revision,
            active=activate,
            source=source,
            metadata=metadata or {},
            created_at=now,
        )

    def relocate_asset(
        self,
        asset_id: int,
        new_path: str | Path,
        *,
        metadata_update: dict[str, Any] | None = None,
    ) -> Asset:
        row = self.store.conn.execute(
            "SELECT * FROM assets WHERE asset_id=?",
            (asset_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Unknown asset: {asset_id}")

        metadata = json.loads(row["metadata_json"] or "{}")
        if metadata_update:
            metadata.update(metadata_update)
        path_str = str(Path(new_path).expanduser().resolve())
        self.store.conn.execute(
            "UPDATE assets SET path=?, metadata_json=? WHERE asset_id=?",
            (path_str, json.dumps(metadata, ensure_ascii=False), asset_id),
        )
        self.store.conn.commit()
        updated = self.store.conn.execute(
            "SELECT * FROM assets WHERE asset_id=?",
            (asset_id,),
        ).fetchone()
        return self._row_to_asset(updated)

    def active_asset(self, project_id: str, scene_id: int, kind: str = "image") -> Asset | None:
        row = self.store.conn.execute(
            """
            SELECT * FROM assets
            WHERE project_id=? AND scene_id=? AND kind=? AND active=1
            ORDER BY revision DESC LIMIT 1
            """,
            (project_id, scene_id, kind),
        ).fetchone()
        return self._row_to_asset(row) if row else None

    def list_assets(self, project_id: str, scene_id: int, kind: str | None = None) -> list[Asset]:
        if kind:
            rows = self.store.conn.execute(
                """
                SELECT * FROM assets WHERE project_id=? AND scene_id=? AND kind=?
                ORDER BY revision DESC
                """,
                (project_id, scene_id, kind),
            ).fetchall()
        else:
            rows = self.store.conn.execute(
                """
                SELECT * FROM assets WHERE project_id=? AND scene_id=?
                ORDER BY kind, revision DESC
                """,
                (project_id, scene_id),
            ).fetchall()
        return [self._row_to_asset(row) for row in rows]

    def set_scene_status(self, project_id: str, scene_id: int, status: str, note: str = "") -> None:
        if status not in VALID_SCENE_STATUSES:
            raise ValueError(f"Unsupported scene status: {status}")
        scene = self.store.get_scene(project_id, scene_id)
        if scene is None:
            raise KeyError(f"Unknown scene {project_id}:{scene_id}")
        scene.status = status
        self.store.upsert_scene(scene, action="set_status", note=note or status)

    def mark_for_regeneration(self, project_id: str, scene_id: int, note: str = "") -> None:
        self.set_scene_status(
            project_id,
            scene_id,
            "needs_regeneration",
            note or "Scene marked for regeneration",
        )

    def approve_scene(self, project_id: str, scene_id: int, note: str = "") -> None:
        if self.active_asset(project_id, scene_id) is None:
            raise RuntimeError("Cannot approve scene without an active asset")
        self.set_scene_status(project_id, scene_id, "approved", note or "Scene approved")

    @staticmethod
    def _row_to_asset(row: Any) -> Asset:
        return Asset(
            asset_id=int(row["asset_id"]),
            project_id=row["project_id"],
            scene_id=int(row["scene_id"]),
            kind=row["kind"],
            path=row["path"],
            status=row["status"],
            revision=int(row["revision"]),
            active=bool(row["active"]),
            source=row["source"],
            metadata=json.loads(row["metadata_json"] or "{}"),
            created_at=row["created_at"],
        )
