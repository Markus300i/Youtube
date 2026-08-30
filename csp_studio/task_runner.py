from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import yaml

from .pipeline_state import invalidate_after_image_change, invalidate_after_stage, mark_done
from .scene_ops import SceneOperations
from .store import StudioStore
from .task_engine import TaskEngine

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_ROOT = Path(os.getenv("CSP_OUTPUT_DIR", str(ROOT / "output"))).expanduser().resolve()
DEFAULT_DB_PATH = Path(os.getenv("CSP_STUDIO_DB", str(DEFAULT_OUTPUT_ROOT / "csp-studio.db"))).expanduser().resolve()

SUPPORTED_STAGES = {
    "regenerate_image",
    "tts",
    "captions",
    "sound_design",
    "visual_qa",
    "opencut_export",
    "render_final",
}


def _slug(value: str) -> str:
    import re
    import unicodedata

    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-zA-Z0-9]+", "-", ascii_value).strip("-").lower() or "project"


class StudioTaskRunner:
    """Execute a fixed allow-list of CSP pipeline tasks."""

    def __init__(
        self,
        db_path: str | Path = DEFAULT_DB_PATH,
        *,
        output_root: str | Path = DEFAULT_OUTPUT_ROOT,
        python_executable: str | None = None,
    ) -> None:
        self.db_path = Path(db_path).expanduser().resolve()
        self.output_root = Path(output_root).expanduser().resolve()
        self.python = python_executable or sys.executable
        self.log_dir = self.output_root / ".studio-tasks"
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def run(self, task_id: str, *, worker_id: str = "studio-web") -> dict[str, Any]:
        with StudioStore(self.db_path) as store:
            engine = TaskEngine(store)
            task = engine.claim(task_id, worker_id)
            if task is None:
                existing = engine.get(task_id)
                if existing is None:
                    raise KeyError(f"Unknown task: {task_id}")
                return existing.to_dict()
            if task.stage not in SUPPORTED_STAGES:
                engine.fail(task_id, f"Unsupported executable stage: {task.stage}")
                return engine.get(task_id).to_dict()  # type: ignore[union-attr]

        log_path = self.log_dir / f"{task_id}.log"
        try:
            result = self._execute(task_id, log_path)
            with StudioStore(self.db_path) as store:
                engine = TaskEngine(store)
                current = engine.get(task_id)
                if current and current.state == "cancelled":
                    return current.to_dict()
                return engine.complete(task_id, result).to_dict()
        except Exception as exc:
            tail = self._tail(log_path)
            message = f"{type(exc).__name__}: {exc}"
            if tail:
                message += f" | log: {tail}"
            with StudioStore(self.db_path) as store:
                engine = TaskEngine(store)
                current = engine.get(task_id)
                if current and current.state == "cancelled":
                    return current.to_dict()
                return engine.fail(task_id, message).to_dict()

    def _execute(self, task_id: str, log_path: Path) -> dict[str, Any]:
        with StudioStore(self.db_path) as store:
            engine = TaskEngine(store)
            task = engine.get(task_id)
            if task is None:
                raise KeyError(task_id)
            project = store.conn.execute("SELECT * FROM projects WHERE project_id=?", (task.project_id,)).fetchone()
            if project is None:
                raise KeyError(f"Unknown project: {task.project_id}")
            engine.progress(task_id, 5, stage="prepare")
            snapshot = None
            if task.stage in {"regenerate_image", "tts", "captions", "sound_design", "render_final"}:
                snapshot = self._write_snapshot(store, task.project_id)

        if task.stage == "regenerate_image":
            if task.scene_id is None:
                raise ValueError("regenerate_image requires scene_id")
            return self._regenerate_scene(task_id, task.project_id, task.scene_id, snapshot, log_path)

        commands = {
            "tts": [self.python, str(ROOT / "scripts" / "generate_tts.py"), str(snapshot)],
            "captions": [self.python, str(ROOT / "scripts" / "transcribe.py"), str(snapshot)],
            "sound_design": [self.python, str(ROOT / "scripts" / "sound_design.py"), str(snapshot)],
            "visual_qa": [self.python, "-m", "csp_studio.visual_qa", task.project_id],
            "opencut_export": [self.python, "-m", "csp_studio.opencut_adapter", task.project_id],
            "render_final": [self.python, str(ROOT / "scripts" / "render.py"), str(snapshot)],
        }
        command = commands[task.stage]
        with StudioStore(self.db_path) as store:
            TaskEngine(store).progress(task_id, 15, stage="execute")
        returncode = self._run_process(task_id, command, log_path, env=self._base_env())
        if returncode != 0:
            raise RuntimeError(f"Command exited with code {returncode}")

        with StudioStore(self.db_path) as store:
            engine = TaskEngine(store)
            engine.progress(task_id, 95, stage="validate_artifacts")
            artifact, artifacts = self._validate_stage_artifacts(store, task.project_id, task.stage)
            if task.stage != "visual_qa":
                mark_done(
                    engine,
                    task.project_id,
                    task.stage,
                    artifact_path=artifact,
                    metadata={"task_id": task_id, "artifacts": [str(path) for path in artifacts]},
                )
                invalidate_after_stage(
                    engine,
                    task.project_id,
                    task.stage,
                    reason=f"{task.stage} regenerated by task {task_id}",
                )

        return {
            "returncode": returncode,
            "log_path": str(log_path),
            "command": self._display_command(command),
            "artifact": str(artifact) if artifact else None,
            "artifacts": [str(path) for path in artifacts],
        }

    def _project_dir(self, store: StudioStore, project_id: str) -> Path:
        project = store.conn.execute("SELECT title FROM projects WHERE project_id=?", (project_id,)).fetchone()
        if project is None:
            raise KeyError(project_id)
        return self.output_root / f"{project_id}-{_slug(project['title'])}"

    @staticmethod
    def _usable_file(path: Path) -> bool:
        return path.is_file() and path.stat().st_size > 0

    def _validate_stage_artifacts(
        self,
        store: StudioStore,
        project_id: str,
        stage: str,
    ) -> tuple[Path | None, list[Path]]:
        if stage == "visual_qa":
            return None, []  # VisualQA owns and validates its checkpoint/report.

        project_dir = self._project_dir(store, project_id)
        if stage == "tts":
            required = [project_dir / "audio" / "voice.wav", project_dir / "audio" / "tts-timings.json"]
            missing = [path for path in required if not self._usable_file(path)]
            if missing:
                raise FileNotFoundError("TTS completed without required artifacts: " + ", ".join(str(path) for path in missing))
            return required[0], required

        if stage == "captions":
            candidates = [project_dir / "subtitles.ass", project_dir / "subtitles.srt"]
            existing = [path for path in candidates if self._usable_file(path)]
            if not existing:
                raise FileNotFoundError("Captions completed without subtitles.ass or subtitles.srt")
            return existing[0], existing

        mapping = {
            "sound_design": project_dir / "audio" / "final_mix.wav",
            "opencut_export": project_dir / "opencut" / "csp-opencut.json",
            "render_final": project_dir / "final.mp4",
        }
        artifact = mapping.get(stage)
        if artifact is None:
            return None, []
        if not self._usable_file(artifact):
            raise FileNotFoundError(f"{stage} completed without required artifact: {artifact}")
        return artifact, [artifact]

    def _regenerate_scene(
        self,
        task_id: str,
        project_id: str,
        scene_id: int,
        snapshot: Path | None,
        log_path: Path,
    ) -> dict[str, Any]:
        if snapshot is None:
            raise RuntimeError("Missing project snapshot")

        base_env = self._base_env()
        if os.name == "nt":
            ensure_script = ROOT / "setup" / "ensure-comfyui.ps1"
            if not ensure_script.is_file():
                raise FileNotFoundError(ensure_script)
            ensure_command = [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(ensure_script),
            ]
            with StudioStore(self.db_path) as store:
                TaskEngine(store).progress(task_id, 10, stage="ensure_comfyui")
            ensure_returncode = self._run_process(task_id, ensure_command, log_path, env=base_env)
            if ensure_returncode != 0:
                raise RuntimeError(
                    "ComfyUI could not be started. Start it manually or set CSP_COMFYUI_PATH / CSP_COMFY_PYTHON."
                )

        with tempfile.TemporaryDirectory(prefix="csp-studio-regen-") as tmp:
            temp_output = Path(tmp) / "output"
            env = dict(base_env)
            env["CSP_OUTPUT_DIR"] = str(temp_output)

            with StudioStore(self.db_path) as store:
                project = store.conn.execute("SELECT title FROM projects WHERE project_id=?", (project_id,)).fetchone()
                if project is None:
                    raise KeyError(project_id)
                slug = f"{project_id}-{_slug(project['title'])}"
                real_images = self.output_root / slug / "images"
                sandbox_images = temp_output / slug / "images"
                sandbox_images.mkdir(parents=True, exist_ok=True)
                copied_refs: list[int] = []
                for scene in store.list_scenes(project_id):
                    if scene.scene_id == scene_id:
                        continue
                    source = real_images / f"scene-{scene.scene_id:02d}.png"
                    if not source.is_file() or source.stat().st_size <= 0:
                        continue
                    shutil.copy2(source, sandbox_images / source.name)
                    copied_refs.append(scene.scene_id)
                TaskEngine(store).progress(task_id, 15, stage="prepare_references")

            with log_path.open("a", encoding="utf-8", errors="replace") as log:
                log.write(
                    "REFERENCE SCENES COPIED: "
                    + (", ".join(str(item) for item in copied_refs) if copied_refs else "none")
                    + "\n"
                )

            command = [self.python, str(ROOT / "scripts" / "generate_scene.py"), str(snapshot), str(scene_id)]
            with StudioStore(self.db_path) as store:
                TaskEngine(store).progress(task_id, 20, stage="generate_image")
            returncode = self._run_process(task_id, command, log_path, env=env)
            if returncode != 0:
                raise RuntimeError(f"Scene generator exited with code {returncode}")

            with StudioStore(self.db_path) as store:
                project = store.conn.execute("SELECT title FROM projects WHERE project_id=?", (project_id,)).fetchone()
                if project is None:
                    raise KeyError(project_id)
                generated = temp_output / f"{project_id}-{_slug(project['title'])}" / "images" / f"scene-{scene_id:02d}.png"
                if not generated.is_file() or generated.stat().st_size <= 0:
                    raise FileNotFoundError(generated)
                engine = TaskEngine(store)
                engine.progress(task_id, 85, stage="activate_revision")
                real_images = self.output_root / f"{project_id}-{_slug(project['title'])}" / "images"
                asset = SceneOperations(store, real_images).replace_image(
                    project_id,
                    scene_id,
                    generated,
                    source="local-comfy-regenerate",
                    note="Generated from CSP Studio Regenerate action",
                )
                invalidate_after_image_change(
                    engine,
                    project_id,
                    scene_id=scene_id,
                    reason=f"scene {scene_id} regenerated by task {task_id}",
                )
                return {
                    "returncode": returncode,
                    "log_path": str(log_path),
                    "command": self._display_command(command),
                    "reference_scenes": copied_refs,
                    "asset": asset.to_dict(),
                }

    def _write_snapshot(self, store: StudioStore, project_id: str) -> Path:
        project = store.conn.execute("SELECT * FROM projects WHERE project_id=?", (project_id,)).fetchone()
        if project is None:
            raise KeyError(project_id)
        source = str(project["source_yaml"] or "").strip()
        if not source:
            raise RuntimeError("Project has no source_yaml; executable pipeline requires the imported short YAML")
        source_path = Path(source).expanduser().resolve()
        if not source_path.is_file():
            raise FileNotFoundError(source_path)
        payload = yaml.safe_load(source_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"Invalid source YAML: {source_path}")

        payload["id"] = project_id
        payload["title"] = project["title"]
        payload["series"] = project["series"]
        payload["fictional"] = bool(project["fictional"])
        payload["status"] = project["status"]
        payload["narration"] = project["narration"]
        payload["visual_style"] = project["visual_style"]
        original = {int(item.get("id", 0)): dict(item) for item in (payload.get("scenes") or []) if isinstance(item, dict)}
        scenes = []
        for scene in store.list_scenes(project_id):
            row = original.get(scene.scene_id, {})
            row.update(
                {
                    "id": scene.scene_id,
                    "text": scene.text,
                    "prompt": scene.prompt,
                    "motion": scene.motion,
                    "continuity_refs": list(scene.continuity_refs),
                    "render": dict(scene.render),
                }
            )
            scenes.append(row)
        payload["scenes"] = scenes

        snapshot_dir = self.output_root / ".studio-snapshots"
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        target = snapshot_dir / f"{project_id}-current.yaml"
        temp = target.with_suffix(".yaml.tmp")
        temp.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
        os.replace(temp, target)
        return target

    def _run_process(self, task_id: str, command: list[str], log_path: Path, *, env: dict[str, str]) -> int:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8", errors="replace") as log:
            log.write("\n" + "=" * 72 + "\n")
            log.write("COMMAND: " + self._display_command(command) + "\n\n")
            log.flush()
            process = subprocess.Popen(
                command,
                cwd=ROOT,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
            )
            while process.poll() is None:
                time.sleep(0.5)
                with StudioStore(self.db_path) as store:
                    current = TaskEngine(store).get(task_id)
                    if current and current.state == "cancelled":
                        process.terminate()
                        try:
                            process.wait(timeout=8)
                        except subprocess.TimeoutExpired:
                            process.kill()
                        return int(process.returncode or -1)
            return int(process.returncode or 0)

    def _base_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["CSP_OUTPUT_DIR"] = str(self.output_root)
        env["CSP_STUDIO_DB"] = str(self.db_path)
        return env

    @staticmethod
    def _display_command(command: list[str]) -> str:
        return " ".join(str(part) for part in command)

    @staticmethod
    def _tail(path: Path, chars: int = 1800) -> str:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        return text[-chars:].replace("\n", " | ")


def run_task(task_id: str, *, db_path: str | Path = DEFAULT_DB_PATH, output_root: str | Path = DEFAULT_OUTPUT_ROOT) -> dict[str, Any]:
    return StudioTaskRunner(db_path, output_root=output_root).run(task_id)


def run_task_waiting(
    task_id: str,
    *,
    db_path: str | Path = DEFAULT_DB_PATH,
    output_root: str | Path = DEFAULT_OUTPUT_ROOT,
    poll_seconds: float = 2.0,
    max_wait_seconds: float = 7200.0,
) -> dict[str, Any]:
    """Run one queued task, waiting for a serialized resource such as the GPU."""

    deadline = time.time() + max_wait_seconds
    while True:
        result = run_task(task_id, db_path=db_path, output_root=output_root)
        state = str(result.get("state") or "")
        if state != "queued":
            return result
        if time.time() >= deadline:
            with StudioStore(db_path) as store:
                engine = TaskEngine(store)
                current = engine.get(task_id)
                if current and current.state == "queued":
                    return engine.fail(
                        task_id,
                        f"Timed out waiting {int(max_wait_seconds)}s for resource {current.resource}",
                        failed_stage="queue_wait",
                    ).to_dict()
                return current.to_dict() if current else result
        time.sleep(max(0.2, poll_seconds))
