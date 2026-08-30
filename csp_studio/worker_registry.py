from __future__ import annotations

import json
import os
import socket
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .models import utc_now
from .store import StudioStore

WORKER_SCHEMA = """
CREATE TABLE IF NOT EXISTS studio_workers (
    worker_id TEXT PRIMARY KEY,
    hostname TEXT NOT NULL,
    pid INTEGER NOT NULL,
    state TEXT NOT NULL,
    current_task_id TEXT,
    started_at TEXT NOT NULL,
    heartbeat_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_studio_workers_heartbeat
ON studio_workers(heartbeat_at);
"""

VALID_WORKER_STATES = {"starting", "idle", "running", "stopping", "stopped", "error"}


@dataclass(slots=True)
class WorkerStatus:
    worker_id: str
    hostname: str
    pid: int
    state: str
    current_task_id: str | None
    started_at: str
    heartbeat_at: str
    metadata: dict[str, Any] = field(default_factory=dict)
    online: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "worker_id": self.worker_id,
            "hostname": self.hostname,
            "pid": self.pid,
            "state": self.state,
            "current_task_id": self.current_task_id,
            "started_at": self.started_at,
            "heartbeat_at": self.heartbeat_at,
            "metadata": dict(self.metadata),
            "online": self.online,
        }


class WorkerRegistry:
    """Durable process heartbeat registry for the Studio execution plane."""

    def __init__(self, store: StudioStore):
        self.store = store
        self.store.conn.executescript(WORKER_SCHEMA)
        self.store.conn.commit()

    def heartbeat(
        self,
        worker_id: str,
        *,
        state: str,
        current_task_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        hostname: str | None = None,
        pid: int | None = None,
    ) -> WorkerStatus:
        if state not in VALID_WORKER_STATES:
            raise ValueError(f"Unsupported worker state: {state}")
        if not str(worker_id).strip():
            raise ValueError("worker_id is required")
        now = utc_now()
        host = hostname or socket.gethostname()
        process_id = int(pid if pid is not None else os.getpid())
        previous = self.store.conn.execute(
            "SELECT started_at,metadata_json FROM studio_workers WHERE worker_id=?",
            (worker_id,),
        ).fetchone()
        started_at = str(previous["started_at"]) if previous else now
        merged_metadata: dict[str, Any] = {}
        if previous:
            try:
                merged_metadata.update(json.loads(previous["metadata_json"] or "{}"))
            except (TypeError, json.JSONDecodeError):
                pass
        if metadata:
            merged_metadata.update(metadata)
        self.store.conn.execute(
            """
            INSERT INTO studio_workers(
                worker_id,hostname,pid,state,current_task_id,started_at,heartbeat_at,metadata_json
            ) VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(worker_id) DO UPDATE SET
                hostname=excluded.hostname,
                pid=excluded.pid,
                state=excluded.state,
                current_task_id=excluded.current_task_id,
                heartbeat_at=excluded.heartbeat_at,
                metadata_json=excluded.metadata_json
            """,
            (
                worker_id,
                host,
                process_id,
                state,
                current_task_id,
                started_at,
                now,
                json.dumps(merged_metadata, ensure_ascii=False),
            ),
        )
        self.store.conn.commit()
        return self.get(worker_id, online_ttl_seconds=30)  # type: ignore[return-value]

    def get(self, worker_id: str, *, online_ttl_seconds: float = 30.0) -> WorkerStatus | None:
        row = self.store.conn.execute(
            "SELECT * FROM studio_workers WHERE worker_id=?",
            (worker_id,),
        ).fetchone()
        return self._row(row, online_ttl_seconds) if row else None

    def list(self, *, online_ttl_seconds: float = 30.0) -> list[WorkerStatus]:
        rows = self.store.conn.execute(
            "SELECT * FROM studio_workers ORDER BY heartbeat_at DESC, worker_id"
        ).fetchall()
        return [self._row(row, online_ttl_seconds) for row in rows]

    def mark_stopped(self, worker_id: str, *, error: str | None = None) -> WorkerStatus | None:
        current = self.get(worker_id)
        if current is None:
            return None
        metadata = dict(current.metadata)
        if error:
            metadata["last_error"] = str(error)[:1000]
        return self.heartbeat(
            worker_id,
            state="error" if error else "stopped",
            current_task_id=None,
            metadata=metadata,
            hostname=current.hostname,
            pid=current.pid,
        )

    @staticmethod
    def _parse(value: str) -> datetime | None:
        try:
            parsed = datetime.fromisoformat(value)
        except (TypeError, ValueError):
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def _row(self, row, online_ttl_seconds: float) -> WorkerStatus:
        heartbeat = self._parse(str(row["heartbeat_at"]))
        age = (datetime.now(timezone.utc) - heartbeat).total_seconds() if heartbeat else float("inf")
        state = str(row["state"])
        online = age <= max(1.0, float(online_ttl_seconds)) and state not in {"stopped", "error"}
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            metadata = {}
        return WorkerStatus(
            worker_id=str(row["worker_id"]),
            hostname=str(row["hostname"]),
            pid=int(row["pid"]),
            state=state,
            current_task_id=str(row["current_task_id"]) if row["current_task_id"] else None,
            started_at=str(row["started_at"]),
            heartbeat_at=str(row["heartbeat_at"]),
            metadata=metadata,
            online=online,
        )
