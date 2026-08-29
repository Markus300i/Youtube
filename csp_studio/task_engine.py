from __future__ import annotations

import argparse
import json
import os
import sqlite3
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .models import utc_now
from .store import StudioStore

ROOT = Path(__file__).resolve().parents[1]

TASK_SCHEMA = """
CREATE TABLE IF NOT EXISTS studio_tasks (
    task_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    scene_id INTEGER,
    stage TEXT NOT NULL,
    resource TEXT NOT NULL DEFAULT 'cpu',
    state TEXT NOT NULL DEFAULT 'queued',
    progress INTEGER NOT NULL DEFAULT 0,
    retry_count INTEGER NOT NULL DEFAULT 0,
    worker_id TEXT,
    failed_stage TEXT,
    error TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}',
    result_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_studio_tasks_queue
ON studio_tasks(state, resource, created_at);

CREATE INDEX IF NOT EXISTS idx_studio_tasks_project
ON studio_tasks(project_id, stage, scene_id, created_at DESC);

CREATE TABLE IF NOT EXISTS pipeline_checkpoints (
    project_id TEXT NOT NULL,
    scene_id INTEGER NOT NULL DEFAULT 0,
    stage TEXT NOT NULL,
    state TEXT NOT NULL,
    artifact_path TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (project_id, scene_id, stage),
    FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE
);
"""

VALID_STATES = {"queued", "running", "succeeded", "failed", "cancelled"}
VALID_RESOURCES = {"cpu", "gpu", "io", "network"}


@dataclass(slots=True)
class StudioTask:
    task_id: str
    project_id: str
    stage: str
    scene_id: int | None = None
    resource: str = "cpu"
    state: str = "queued"
    progress: int = 0
    retry_count: int = 0
    worker_id: str | None = None
    failed_stage: str | None = None
    error: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)
    started_at: str | None = None
    finished_at: str | None = None
    updated_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class TaskEngine:
    """Persistent Studio task state and queue.

    This intentionally does not spawn background threads yet. It provides the durable
    contract that GUI workers, Agent One and future OpenCut/render workers can share.
    GPU claims are serialized to one running task at a time for the 8 GB CSP machine.
    """

    def __init__(self, store: StudioStore):
        self.store = store
        self.store.conn.executescript(TASK_SCHEMA)
        self.store.conn.commit()

    def submit(
        self,
        project_id: str,
        stage: str,
        *,
        scene_id: int | None = None,
        resource: str = "cpu",
        payload: dict[str, Any] | None = None,
        task_id: str | None = None,
    ) -> StudioTask:
        if resource not in VALID_RESOURCES:
            raise ValueError(f"Unsupported resource: {resource}")
        if self.store.conn.execute("SELECT 1 FROM projects WHERE project_id=?", (project_id,)).fetchone() is None:
            raise KeyError(f"Unknown project: {project_id}")
        now = utc_now()
        task_id = task_id or f"{project_id}-{stage}-{uuid.uuid4().hex[:10]}"
        self.store.conn.execute(
            """
            INSERT INTO studio_tasks (
                task_id,project_id,scene_id,stage,resource,state,progress,retry_count,
                payload_json,result_json,created_at,updated_at
            ) VALUES (?,?,?,?,?,'queued',0,0,?,'{}',?,?)
            """,
            (
                task_id,
                project_id,
                scene_id,
                stage,
                resource,
                json.dumps(payload or {}, ensure_ascii=False),
                now,
                now,
            ),
        )
        self.store.conn.commit()
        task = self.get(task_id)
        assert task is not None
        return task

    def get(self, task_id: str) -> StudioTask | None:
        row = self.store.conn.execute("SELECT * FROM studio_tasks WHERE task_id=?", (task_id,)).fetchone()
        return self._row_to_task(row) if row else None

    def list(self, project_id: str | None = None, state: str | None = None) -> list[StudioTask]:
        where: list[str] = []
        params: list[Any] = []
        if project_id:
            where.append("project_id=?")
            params.append(project_id)
        if state:
            if state not in VALID_STATES:
                raise ValueError(f"Unsupported state: {state}")
            where.append("state=?")
            params.append(state)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        rows = self.store.conn.execute(
            f"SELECT * FROM studio_tasks {clause} ORDER BY created_at DESC",
            tuple(params),
        ).fetchall()
        return [self._row_to_task(row) for row in rows]

    def claim_next(self, worker_id: str, *, resource: str | None = None) -> StudioTask | None:
        if resource is not None and resource not in VALID_RESOURCES:
            raise ValueError(f"Unsupported resource: {resource}")
        conn = self.store.conn
        conn.execute("BEGIN IMMEDIATE")
        try:
            if resource == "gpu":
                running_gpu = conn.execute(
                    "SELECT 1 FROM studio_tasks WHERE state='running' AND resource='gpu' LIMIT 1"
                ).fetchone()
                if running_gpu:
                    conn.rollback()
                    return None

            if resource:
                row = conn.execute(
                    """
                    SELECT task_id FROM studio_tasks
                    WHERE state='queued' AND resource=?
                    ORDER BY created_at ASC LIMIT 1
                    """,
                    (resource,),
                ).fetchone()
            else:
                running_gpu = conn.execute(
                    "SELECT 1 FROM studio_tasks WHERE state='running' AND resource='gpu' LIMIT 1"
                ).fetchone()
                if running_gpu:
                    row = conn.execute(
                        """
                        SELECT task_id FROM studio_tasks
                        WHERE state='queued' AND resource!='gpu'
                        ORDER BY created_at ASC LIMIT 1
                        """
                    ).fetchone()
                else:
                    row = conn.execute(
                        "SELECT task_id FROM studio_tasks WHERE state='queued' ORDER BY created_at ASC LIMIT 1"
                    ).fetchone()

            if row is None:
                conn.rollback()
                return None

            now = utc_now()
            conn.execute(
                """
                UPDATE studio_tasks
                SET state='running', worker_id=?, started_at=?, finished_at=NULL,
                    progress=0, failed_stage=NULL, error=NULL, updated_at=?
                WHERE task_id=? AND state='queued'
                """,
                (worker_id, now, now, row["task_id"]),
            )
            conn.commit()
            return self.get(row["task_id"])
        except Exception:
            conn.rollback()
            raise

    def progress(self, task_id: str, value: int, *, stage: str | None = None) -> StudioTask:
        task = self._require(task_id)
        if task.state != "running":
            raise RuntimeError(f"Task {task_id} is not running")
        value = max(0, min(100, int(value)))
        now = utc_now()
        self.store.conn.execute(
            "UPDATE studio_tasks SET progress=?, failed_stage=COALESCE(?, failed_stage), updated_at=? WHERE task_id=?",
            (value, stage, now, task_id),
        )
        self.store.conn.commit()
        return self._require(task_id)

    def complete(self, task_id: str, result: dict[str, Any] | None = None) -> StudioTask:
        task = self._require(task_id)
        if task.state != "running":
            raise RuntimeError(f"Task {task_id} is not running")
        now = utc_now()
        self.store.conn.execute(
            """
            UPDATE studio_tasks
            SET state='succeeded', progress=100, result_json=?, finished_at=?, updated_at=?,
                failed_stage=NULL, error=NULL
            WHERE task_id=?
            """,
            (json.dumps(result or {}, ensure_ascii=False), now, now, task_id),
        )
        self.store.conn.commit()
        return self._require(task_id)

    def fail(self, task_id: str, error: str, *, failed_stage: str | None = None) -> StudioTask:
        task = self._require(task_id)
        if task.state not in {"running", "queued"}:
            raise RuntimeError(f"Task {task_id} cannot fail from state {task.state}")
        now = utc_now()
        self.store.conn.execute(
            """
            UPDATE studio_tasks
            SET state='failed', failed_stage=?, error=?, finished_at=?, updated_at=?
            WHERE task_id=?
            """,
            (failed_stage or task.stage, error, now, now, task_id),
        )
        self.store.conn.commit()
        return self._require(task_id)

    def retry(self, task_id: str) -> StudioTask:
        task = self._require(task_id)
        if task.state != "failed":
            raise RuntimeError(f"Task {task_id} is not failed")
        now = utc_now()
        self.store.conn.execute(
            """
            UPDATE studio_tasks
            SET state='queued', progress=0, retry_count=retry_count+1, worker_id=NULL,
                started_at=NULL, finished_at=NULL, failed_stage=NULL, error=NULL, updated_at=?
            WHERE task_id=?
            """,
            (now, task_id),
        )
        self.store.conn.commit()
        return self._require(task_id)

    def cancel(self, task_id: str) -> StudioTask:
        task = self._require(task_id)
        if task.state not in {"queued", "running"}:
            raise RuntimeError(f"Task {task_id} cannot be cancelled from state {task.state}")
        now = utc_now()
        self.store.conn.execute(
            "UPDATE studio_tasks SET state='cancelled', finished_at=?, updated_at=? WHERE task_id=?",
            (now, now, task_id),
        )
        self.store.conn.commit()
        return self._require(task_id)

    def set_checkpoint(
        self,
        project_id: str,
        stage: str,
        state: str,
        *,
        scene_id: int | None = None,
        artifact_path: str | Path | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if state not in {"waiting", "running", "done", "failed", "stale"}:
            raise ValueError(f"Unsupported checkpoint state: {state}")
        sid = int(scene_id or 0)
        path_str = str(Path(artifact_path).expanduser().resolve()) if artifact_path else None
        now = utc_now()
        self.store.conn.execute(
            """
            INSERT INTO pipeline_checkpoints (
                project_id,scene_id,stage,state,artifact_path,metadata_json,updated_at
            ) VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(project_id,scene_id,stage) DO UPDATE SET
                state=excluded.state,
                artifact_path=excluded.artifact_path,
                metadata_json=excluded.metadata_json,
                updated_at=excluded.updated_at
            """,
            (project_id, sid, stage, state, path_str, json.dumps(metadata or {}, ensure_ascii=False), now),
        )
        self.store.conn.commit()
        return self.get_checkpoint(project_id, stage, scene_id=scene_id) or {}

    def get_checkpoint(self, project_id: str, stage: str, *, scene_id: int | None = None) -> dict[str, Any] | None:
        sid = int(scene_id or 0)
        row = self.store.conn.execute(
            "SELECT * FROM pipeline_checkpoints WHERE project_id=? AND scene_id=? AND stage=?",
            (project_id, sid, stage),
        ).fetchone()
        if row is None:
            return None
        return {
            "project_id": row["project_id"],
            "scene_id": None if int(row["scene_id"]) == 0 else int(row["scene_id"]),
            "stage": row["stage"],
            "state": row["state"],
            "artifact_path": row["artifact_path"],
            "metadata": json.loads(row["metadata_json"] or "{}"),
            "updated_at": row["updated_at"],
        }

    def checkpoint_is_usable(self, project_id: str, stage: str, *, scene_id: int | None = None) -> bool:
        checkpoint = self.get_checkpoint(project_id, stage, scene_id=scene_id)
        if not checkpoint or checkpoint["state"] != "done":
            return False
        artifact = checkpoint.get("artifact_path")
        return not artifact or Path(artifact).is_file()

    def _require(self, task_id: str) -> StudioTask:
        task = self.get(task_id)
        if task is None:
            raise KeyError(f"Unknown task: {task_id}")
        return task

    @staticmethod
    def _row_to_task(row: sqlite3.Row) -> StudioTask:
        return StudioTask(
            task_id=row["task_id"],
            project_id=row["project_id"],
            scene_id=int(row["scene_id"]) if row["scene_id"] is not None else None,
            stage=row["stage"],
            resource=row["resource"],
            state=row["state"],
            progress=int(row["progress"]),
            retry_count=int(row["retry_count"]),
            worker_id=row["worker_id"],
            failed_stage=row["failed_stage"],
            error=row["error"],
            payload=json.loads(row["payload_json"] or "{}"),
            result=json.loads(row["result_json"] or "{}"),
            created_at=row["created_at"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            updated_at=row["updated_at"],
        )


def atomic_write_json(path: str | Path, payload: dict[str, Any]) -> Path:
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temp.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description="CSP Studio persistent task engine")
    parser.add_argument("--db", default=os.getenv("CSP_STUDIO_DB"))
    sub = parser.add_subparsers(dest="command", required=True)

    list_cmd = sub.add_parser("list")
    list_cmd.add_argument("--project")
    list_cmd.add_argument("--state", choices=sorted(VALID_STATES))

    submit_cmd = sub.add_parser("submit")
    submit_cmd.add_argument("project_id")
    submit_cmd.add_argument("stage")
    submit_cmd.add_argument("--scene", type=int)
    submit_cmd.add_argument("--resource", choices=sorted(VALID_RESOURCES), default="cpu")

    args = parser.parse_args()
    if args.db:
        db_path = Path(args.db).expanduser().resolve()
    else:
        output_root = Path(os.getenv("CSP_OUTPUT_DIR", str(ROOT / "output"))).expanduser().resolve()
        db_path = output_root / "csp-studio.db"

    with StudioStore(db_path) as store:
        engine = TaskEngine(store)
        if args.command == "list":
            for task in engine.list(args.project, args.state):
                scene = f" scene={task.scene_id}" if task.scene_id is not None else ""
                print(f"{task.task_id} {task.state:9s} {task.progress:3d}% {task.resource:7s} {task.stage}{scene}")
        elif args.command == "submit":
            task = engine.submit(args.project_id, args.stage, scene_id=args.scene, resource=args.resource)
            print(task.task_id)


if __name__ == "__main__":
    main()
