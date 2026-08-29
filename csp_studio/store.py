from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .models import Project, Scene, ShotPlan, utc_now


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS projects (
    project_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    series TEXT NOT NULL DEFAULT '',
    fictional INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'draft',
    narration TEXT NOT NULL DEFAULT '',
    visual_style TEXT NOT NULL DEFAULT '',
    source_yaml TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scenes (
    project_id TEXT NOT NULL,
    scene_id INTEGER NOT NULL,
    text TEXT NOT NULL,
    prompt TEXT NOT NULL,
    continuity_refs_json TEXT NOT NULL DEFAULT '[]',
    render_json TEXT NOT NULL DEFAULT '{}',
    motion TEXT NOT NULL DEFAULT 'static',
    shot_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'draft',
    asset_path TEXT,
    revision INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (project_id, scene_id),
    FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS scene_revisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    scene_id INTEGER NOT NULL,
    revision INTEGER NOT NULL,
    action TEXT NOT NULL,
    before_json TEXT NOT NULL,
    after_json TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    FOREIGN KEY (project_id, scene_id) REFERENCES scenes(project_id, scene_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_scene_revisions_scene
ON scene_revisions(project_id, scene_id, revision DESC);
"""


class StudioStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "StudioStore":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def upsert_project(self, project: Project) -> None:
        self.conn.execute(
            """
            INSERT INTO projects (
                project_id,title,series,fictional,status,narration,visual_style,
                source_yaml,created_at,updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(project_id) DO UPDATE SET
                title=excluded.title,
                series=excluded.series,
                fictional=excluded.fictional,
                status=excluded.status,
                narration=excluded.narration,
                visual_style=excluded.visual_style,
                source_yaml=excluded.source_yaml,
                updated_at=excluded.updated_at
            """,
            (
                project.project_id,
                project.title,
                project.series,
                int(project.fictional),
                project.status,
                project.narration,
                project.visual_style,
                project.source_yaml,
                project.created_at,
                project.updated_at,
            ),
        )
        for scene in project.scenes:
            self.upsert_scene(scene, record_revision=False)
        self.conn.commit()

    def upsert_scene(
        self,
        scene: Scene,
        *,
        action: str = "update",
        note: str = "",
        record_revision: bool = True,
    ) -> None:
        before = self.get_scene(scene.project_id, scene.scene_id)
        now = utc_now()
        if before:
            scene.created_at = before.created_at
            scene.updated_at = now
            if record_revision:
                scene.revision = before.revision + 1
            else:
                # Re-importing a legacy YAML must never rewind Studio history.
                # YAML is a compatibility/source input; SQLite owns revisions.
                scene.revision = before.revision

        self.conn.execute(
            """
            INSERT INTO scenes (
                project_id,scene_id,text,prompt,continuity_refs_json,render_json,
                motion,shot_json,status,asset_path,revision,created_at,updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(project_id,scene_id) DO UPDATE SET
                text=excluded.text,
                prompt=excluded.prompt,
                continuity_refs_json=excluded.continuity_refs_json,
                render_json=excluded.render_json,
                motion=excluded.motion,
                shot_json=excluded.shot_json,
                status=excluded.status,
                asset_path=COALESCE(excluded.asset_path, scenes.asset_path),
                revision=excluded.revision,
                updated_at=excluded.updated_at
            """,
            (
                scene.project_id,
                scene.scene_id,
                scene.text,
                scene.prompt,
                json.dumps(scene.continuity_refs, ensure_ascii=False),
                json.dumps(scene.render, ensure_ascii=False),
                scene.motion,
                json.dumps(scene.shot.to_dict(), ensure_ascii=False),
                scene.status,
                scene.asset_path,
                scene.revision,
                scene.created_at,
                scene.updated_at,
            ),
        )

        if before and record_revision:
            self.conn.execute(
                """
                INSERT INTO scene_revisions (
                    project_id,scene_id,revision,action,before_json,after_json,note,created_at
                ) VALUES (?,?,?,?,?,?,?,?)
                """,
                (
                    scene.project_id,
                    scene.scene_id,
                    scene.revision,
                    action,
                    json.dumps(before.to_dict(), ensure_ascii=False),
                    json.dumps(scene.to_dict(), ensure_ascii=False),
                    note,
                    now,
                ),
            )
        self.conn.commit()

    def get_scene(self, project_id: str, scene_id: int) -> Scene | None:
        row = self.conn.execute(
            "SELECT * FROM scenes WHERE project_id=? AND scene_id=?",
            (project_id, scene_id),
        ).fetchone()
        if row is None:
            return None
        shot_data = json.loads(row["shot_json"] or "{}")
        allowed_shot_fields = ShotPlan.__dataclass_fields__
        return Scene(
            project_id=row["project_id"],
            scene_id=int(row["scene_id"]),
            text=row["text"],
            prompt=row["prompt"],
            continuity_refs=json.loads(row["continuity_refs_json"] or "[]"),
            render=json.loads(row["render_json"] or "{}"),
            motion=row["motion"],
            shot=ShotPlan(**{k: v for k, v in shot_data.items() if k in allowed_shot_fields}),
            status=row["status"],
            asset_path=row["asset_path"],
            revision=int(row["revision"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def list_scenes(self, project_id: str) -> list[Scene]:
        rows = self.conn.execute(
            "SELECT scene_id FROM scenes WHERE project_id=? ORDER BY scene_id",
            (project_id,),
        ).fetchall()
        result: list[Scene] = []
        for row in rows:
            scene = self.get_scene(project_id, int(row["scene_id"]))
            if scene:
                result.append(scene)
        return result

    def list_revisions(self, project_id: str, scene_id: int) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT revision,action,before_json,after_json,note,created_at
            FROM scene_revisions
            WHERE project_id=? AND scene_id=?
            ORDER BY revision DESC
            """,
            (project_id, scene_id),
        ).fetchall()
        return [
            {
                "revision": int(row["revision"]),
                "action": row["action"],
                "before": json.loads(row["before_json"]),
                "after": json.loads(row["after_json"]),
                "note": row["note"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]
