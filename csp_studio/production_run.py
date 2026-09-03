from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from typing import Any

from .agent_one import AgentOne
from .models import utc_now
from .store import StudioStore
from .task_engine import TaskEngine

RUN_SCHEMA = """
CREATE TABLE IF NOT EXISTS production_runs (
    project_id TEXT PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 0,
    state TEXT NOT NULL DEFAULT 'idle',
    stop_reason TEXT NOT NULL DEFAULT '',
    last_task_id TEXT,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE
);
"""

ACTION_TO_STAGE: dict[str, tuple[str, str]] = {
    "generate_tts": ("tts", "gpu"),
    "generate_captions": ("captions", "gpu"),
    "sound_design": ("sound_design", "cpu"),
    "visual_qa": ("visual_qa", "network"),
    "export_opencut": ("opencut_export", "io"),
    "render_final": ("render_final", "gpu"),
}

HUMAN_GATES = {"fix_scene_plan", "complete_images", "review_scenes"}


@dataclass(slots=True)
class ProductionRunState:
    project_id: str
    enabled: bool
    state: str
    stop_reason: str = ""
    last_task_id: str | None = None
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "enabled": self.enabled,
            "state": self.state,
            "stop_reason": self.stop_reason,
            "last_task_id": self.last_task_id,
            "updated_at": self.updated_at,
        }


class ProductionRunCoordinator:
    """Advance a project through Agent One stages without bypassing human gates.

    This layer only schedules tasks. Execution belongs to StudioWorker / TaskEngine.
    A failed stage stops automatic progression until the user explicitly restarts.
    """

    def __init__(self, store: StudioStore, *, output_root=None):
        self.store = store
        self.store.conn.executescript(RUN_SCHEMA)
        self.store.conn.commit()
        self.tasks = TaskEngine(store)
        self.agent = AgentOne(store, output_root=output_root)

    def _require_project(self, project_id: str) -> None:
        row = self.store.conn.execute("SELECT 1 FROM projects WHERE project_id=?", (project_id,)).fetchone()
        if row is None:
            raise KeyError(f"Unknown project: {project_id}")

    def status(self, project_id: str) -> ProductionRunState:
        self._require_project(project_id)
        row = self.store.conn.execute("SELECT * FROM production_runs WHERE project_id=?", (project_id,)).fetchone()
        if row is None:
            return ProductionRunState(project_id, False, "idle")
        return ProductionRunState(
            project_id=row["project_id"],
            enabled=bool(row["enabled"]),
            state=row["state"],
            stop_reason=row["stop_reason"] or "",
            last_task_id=row["last_task_id"],
            updated_at=row["updated_at"],
        )

    def _save(self, project_id: str, *, enabled: bool, state: str, stop_reason: str = "", last_task_id: str | None = None) -> ProductionRunState:
        now = utc_now()
        self.store.conn.execute(
            """
            INSERT INTO production_runs(project_id,enabled,state,stop_reason,last_task_id,updated_at)
            VALUES (?,?,?,?,?,?)
            ON CONFLICT(project_id) DO UPDATE SET
              enabled=excluded.enabled,
              state=excluded.state,
              stop_reason=excluded.stop_reason,
              last_task_id=excluded.last_task_id,
              updated_at=excluded.updated_at
            """,
            (project_id, int(enabled), state, stop_reason, last_task_id, now),
        )
        self.store.conn.commit()
        return self.status(project_id)

    def start(self, project_id: str) -> dict[str, Any]:
        self._require_project(project_id)
        self._save(project_id, enabled=True, state="running")
        return self.advance(project_id)

    def stop(self, project_id: str, reason: str = "stopped_by_user") -> ProductionRunState:
        current = self.status(project_id)
        return self._save(
            project_id,
            enabled=False,
            state="stopped",
            stop_reason=reason,
            last_task_id=current.last_task_id,
        )

    def advance(self, project_id: str) -> dict[str, Any]:
        current = self.status(project_id)
        if not current.enabled:
            return {"advanced": False, "reason": "not_enabled", "run": current.to_dict()}

        report = self.agent.inspect(project_id)
        action = report.next_action

        if action == "publish_review":
            run = self._save(project_id, enabled=False, state="completed", stop_reason="ready_for_publish")
            return {"advanced": False, "reason": "ready_for_publish", "run": run.to_dict(), "report": report.to_dict()}

        if action in HUMAN_GATES:
            state = "awaiting_review" if action == "review_scenes" else "blocked"
            run = self._save(project_id, enabled=True, state=state, stop_reason=action, last_task_id=current.last_task_id)
            return {"advanced": False, "reason": action, "run": run.to_dict(), "report": report.to_dict()}

        mapped = ACTION_TO_STAGE.get(action)
        if mapped is None:
            run = self._save(project_id, enabled=False, state="failed", stop_reason=f"unsupported_action:{action}")
            return {"advanced": False, "reason": "unsupported_action", "run": run.to_dict(), "report": report.to_dict()}

        stage, resource = mapped
        stage_tasks = [task for task in self.tasks.list(project_id) if task.stage == stage]
        active = next((task for task in stage_tasks if task.state in {"queued", "running"}), None)
        if active is not None:
            run = self._save(project_id, enabled=True, state="waiting_task", last_task_id=active.task_id)
            return {"advanced": False, "reason": "already_active", "task": active.to_dict(), "run": run.to_dict(), "report": report.to_dict()}

        latest = stage_tasks[0] if stage_tasks else None
        if latest is not None and latest.state == "failed":
            run = self._save(project_id, enabled=False, state="failed", stop_reason=f"task_failed:{stage}", last_task_id=latest.task_id)
            return {"advanced": False, "reason": "task_failed", "task": latest.to_dict(), "run": run.to_dict(), "report": report.to_dict()}

        task = self.tasks.submit(
            project_id,
            stage,
            resource=resource,
            payload={"source": "production_run", "readiness_action": action},
        )
        run = self._save(project_id, enabled=True, state="waiting_task", last_task_id=task.task_id)
        return {"advanced": True, "task": task.to_dict(), "run": run.to_dict(), "report": report.to_dict()}

    def advance_enabled(self) -> list[dict[str, Any]]:
        rows = self.store.conn.execute("SELECT project_id FROM production_runs WHERE enabled=1 ORDER BY updated_at").fetchall()
        return [self.advance(str(row["project_id"])) for row in rows]


def main() -> None:
    parser = argparse.ArgumentParser(description="CSP Studio deterministic Production Run coordinator")
    parser.add_argument("--db", required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("start", "advance", "status", "stop"):
        cmd = sub.add_parser(name)
        cmd.add_argument("project_id")
    args = parser.parse_args()
    with StudioStore(args.db) as store:
        coordinator = ProductionRunCoordinator(store)
        if args.command == "start":
            result = coordinator.start(args.project_id)
        elif args.command == "advance":
            result = coordinator.advance(args.project_id)
        elif args.command == "stop":
            result = coordinator.stop(args.project_id).to_dict()
        else:
            result = coordinator.status(args.project_id).to_dict()
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
