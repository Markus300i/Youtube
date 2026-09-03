from __future__ import annotations

import argparse
import json
import os
import socket
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .production_run import ProductionRunCoordinator
from .store import StudioStore
from .task_engine import TaskEngine
from .task_runner import DEFAULT_DB_PATH, DEFAULT_OUTPUT_ROOT, SUPPORTED_STAGES, StudioTaskRunner
from .worker_registry import WorkerRegistry


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class StudioWorker:
    """Persistent execution-plane worker for CSP Studio.

    FastAPI remains the control plane. This worker scans durable SQLite tasks,
    executes one allow-listed task at a time, heartbeats running work and can
    recover abandoned running tasks after a lease timeout. WorkerRegistry keeps
    the process observable even while it is idle.
    """

    def __init__(
        self,
        db_path: str | Path = DEFAULT_DB_PATH,
        *,
        output_root: str | Path = DEFAULT_OUTPUT_ROOT,
        worker_id: str | None = None,
        heartbeat_seconds: float = 5.0,
        lease_seconds: float = 120.0,
        poll_seconds: float = 1.0,
    ) -> None:
        self.db_path = Path(db_path).expanduser().resolve()
        self.output_root = Path(output_root).expanduser().resolve()
        self.worker_id = worker_id or f"{socket.gethostname()}-{os.getpid()}"
        self.heartbeat_seconds = max(0.5, float(heartbeat_seconds))
        self.lease_seconds = max(self.heartbeat_seconds * 2, float(lease_seconds))
        self.poll_seconds = max(0.2, float(poll_seconds))
        self.runner = StudioTaskRunner(self.db_path, output_root=self.output_root)

    def _worker_heartbeat(self, state: str, current_task_id: str | None = None, **metadata: Any) -> None:
        with StudioStore(self.db_path) as store:
            WorkerRegistry(store).heartbeat(
                self.worker_id,
                state=state,
                current_task_id=current_task_id,
                metadata={
                    "db_path": str(self.db_path),
                    "output_root": str(self.output_root),
                    "heartbeat_seconds": self.heartbeat_seconds,
                    "lease_seconds": self.lease_seconds,
                    **metadata,
                },
            )

    def recover_abandoned(self) -> list[str]:
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=self.lease_seconds)
        recovered: list[str] = []
        with StudioStore(self.db_path) as store:
            TaskEngine(store)  # Ensure durable task/checkpoint tables exist on a fresh Studio DB.
            WorkerRegistry(store)  # Ensure process heartbeat table exists too.
            rows = store.conn.execute(
                "SELECT task_id,updated_at FROM studio_tasks WHERE state='running'"
            ).fetchall()
            for row in rows:
                updated = _parse_ts(row["updated_at"])
                if updated is None or updated >= cutoff:
                    continue
                store.conn.execute(
                    """
                    UPDATE studio_tasks
                    SET state='queued', progress=0, worker_id=NULL, started_at=NULL,
                        finished_at=NULL, failed_stage='worker_recovery',
                        error=NULL, updated_at=?
                    WHERE task_id=? AND state='running'
                    """,
                    (datetime.now(timezone.utc).isoformat(timespec="seconds"), row["task_id"]),
                )
                recovered.append(str(row["task_id"]))
            if recovered:
                store.conn.commit()
        return recovered

    def _next_candidate(self) -> str | None:
        with StudioStore(self.db_path) as store:
            ProductionRunCoordinator(store, output_root=self.output_root).advance_enabled()
            placeholders = ",".join("?" for _ in SUPPORTED_STAGES)
            row = store.conn.execute(
                f"""
                SELECT task_id FROM studio_tasks
                WHERE state='queued' AND stage IN ({placeholders})
                ORDER BY created_at ASC LIMIT 1
                """,
                tuple(sorted(SUPPORTED_STAGES)),
            ).fetchone()
            return str(row["task_id"]) if row else None

    def _heartbeat_loop(self, task_id: str, stop: threading.Event) -> None:
        while not stop.wait(self.heartbeat_seconds):
            with StudioStore(self.db_path) as store:
                engine = TaskEngine(store)
                current = engine.get(task_id)
                if current is None or current.state != "running" or current.worker_id != self.worker_id:
                    return
                now = datetime.now(timezone.utc).isoformat(timespec="seconds")
                store.conn.execute(
                    "UPDATE studio_tasks SET updated_at=? WHERE task_id=? AND state='running' AND worker_id=?",
                    (now, task_id, self.worker_id),
                )
                store.conn.commit()
                WorkerRegistry(store).heartbeat(
                    self.worker_id,
                    state="running",
                    current_task_id=task_id,
                    metadata={"task_stage": current.stage, "task_progress": current.progress},
                )

    def run_once(self) -> dict[str, Any] | None:
        recovered = self.recover_abandoned()
        task_id = self._next_candidate()
        if task_id is None:
            self._worker_heartbeat("idle", recovered_tasks=recovered)
            return None

        self._worker_heartbeat("running", task_id, recovered_tasks=recovered)
        stop = threading.Event()
        heartbeat = threading.Thread(
            target=self._heartbeat_loop,
            args=(task_id, stop),
            name=f"csp-heartbeat-{task_id}",
            daemon=True,
        )
        heartbeat.start()
        try:
            result = self.runner.run(task_id, worker_id=self.worker_id)
        finally:
            stop.set()
            heartbeat.join(timeout=max(1.0, self.heartbeat_seconds * 2))

        self._worker_heartbeat("idle", None, last_task_id=task_id, last_task_state=result.get("state"))
        with StudioStore(self.db_path) as store:
            ProductionRunCoordinator(store, output_root=self.output_root).advance_enabled()
        return result

    def run_forever(self) -> None:
        self._worker_heartbeat("starting")
        try:
            while True:
                result = self.run_once()
                if result is None or result.get("state") == "queued":
                    time.sleep(self.poll_seconds)
        except KeyboardInterrupt:
            self._worker_heartbeat("stopping")
        except BaseException as exc:
            with StudioStore(self.db_path) as store:
                WorkerRegistry(store).mark_stopped(self.worker_id, error=f"{type(exc).__name__}: {exc}")
            raise
        finally:
            with StudioStore(self.db_path) as store:
                status = WorkerRegistry(store).get(self.worker_id)
                if status is not None and status.state != "error":
                    WorkerRegistry(store).mark_stopped(self.worker_id)


def main() -> None:
    parser = argparse.ArgumentParser(description="CSP Studio persistent worker")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--worker-id")
    parser.add_argument("--heartbeat-seconds", type=float, default=5.0)
    parser.add_argument("--lease-seconds", type=float, default=120.0)
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    args = parser.parse_args()
    worker = StudioWorker(
        args.db,
        output_root=args.output_root,
        worker_id=args.worker_id,
        heartbeat_seconds=args.heartbeat_seconds,
        lease_seconds=args.lease_seconds,
        poll_seconds=args.poll_seconds,
    )
    if args.once:
        print(json.dumps(worker.run_once(), ensure_ascii=False, indent=2))
    else:
        worker.run_forever()


if __name__ == "__main__":
    main()
