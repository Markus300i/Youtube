from __future__ import annotations

import argparse
import json
import os
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from PIL import Image

from .asset_manager import AssetManager
from .providers import VisionProvider, get_provider
from .providers.base import ProviderError
from .shot_director import ShotDirector
from .store import StudioStore
from .task_engine import TaskEngine, atomic_write_json

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_ROOT = Path(os.getenv("CSP_OUTPUT_DIR", str(ROOT / "output"))).expanduser().resolve()
DB_PATH = Path(os.getenv("CSP_STUDIO_DB", str(OUTPUT_ROOT / "csp-studio.db"))).expanduser().resolve()


def _slug(value: str) -> str:
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-zA-Z0-9]+", "-", ascii_value).strip("-").lower() or "project"


@dataclass(slots=True)
class VisualSceneNote:
    scene_id: int
    severity: str = "info"
    issue: str = ""
    recommendation: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class VisualQAReport:
    project_id: str
    score: int
    provider: str
    model: str
    summary: str = ""
    warnings: list[str] = field(default_factory=list)
    continuity: list[str] = field(default_factory=list)
    monotony: list[str] = field(default_factory=list)
    scene_notes: list[VisualSceneNote] = field(default_factory=list)
    shot_director_score: int = 100
    shot_director_warnings: list[str] = field(default_factory=list)
    raw_text: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["scene_notes"] = [note.to_dict() for note in self.scene_notes]
        return data


class VisualQA:
    def __init__(self, store: StudioStore, *, output_root: str | Path | None = None):
        self.store = store
        self.output_root = Path(output_root or OUTPUT_ROOT).expanduser().resolve()
        self.assets = AssetManager(store)
        self.tasks = TaskEngine(store)

    def _project_row(self, project_id: str):
        row = self.store.conn.execute("SELECT * FROM projects WHERE project_id=?", (project_id,)).fetchone()
        if row is None:
            raise KeyError(f"Unknown project: {project_id}")
        return row

    def _project_dir(self, project_id: str) -> Path:
        row = self._project_row(project_id)
        return self.output_root / f"{project_id}-{_slug(row['title'])}"

    def _review_images(self, project_id: str) -> list[Path]:
        scenes = self.store.list_scenes(project_id)
        if len(scenes) != 8:
            raise RuntimeError(f"Visual QA requires exactly 8 scenes, got {len(scenes)}")
        qa_dir = self._project_dir(project_id) / "qa" / "thumbnails"
        qa_dir.mkdir(parents=True, exist_ok=True)
        output: list[Path] = []
        for scene in scenes:
            asset = self.assets.active_asset(project_id, scene.scene_id, "image")
            if asset is None:
                raise RuntimeError(f"Scene {scene.scene_id:02d} has no active image")
            source = Path(asset.path)
            if not source.is_file():
                raise FileNotFoundError(source)
            target = qa_dir / f"scene-{scene.scene_id:02d}-r{asset.revision}.jpg"
            with Image.open(source) as image:
                converted = image.convert("RGB")
                converted.thumbnail((432, 768), Image.Resampling.LANCZOS)
                converted.save(target, "JPEG", quality=82, optimize=True)
            output.append(target)
        return output

    def _prompt(self, project_id: str) -> str:
        project = self._project_row(project_id)
        scenes = self.store.list_scenes(project_id)
        shot_audit = ShotDirector().audit(scenes)
        scene_context = [
            {
                "scene_id": scene.scene_id,
                "narration": scene.text,
                "shot": scene.shot.to_dict(),
                "motion": scene.motion,
                "continuity_refs": scene.continuity_refs,
            }
            for scene in scenes
        ]
        return (
            "You are the Visual Director QA reviewer for a FICTIONAL Polish documentary-thriller YouTube Short. "
            "The eight attached images are Scene 1 through Scene 8 IN THAT ORDER. Evaluate the actual frames, not only the metadata. "
            "Prioritize: visual variety, repeated camera/framing language, continuity of recurring locations/doors/characters, "
            "believable documentary realism, AI-looking anatomy/faces/hands, visual clarity on a 9:16 phone screen, and whether each frame supports its narration. "
            "Do not request gore or supernatural creatures. A recurring visual element should remain consistent, but continuity must not become eight nearly identical shots. "
            "Return ONLY valid JSON, with no markdown fences and no prose outside JSON, in this exact shape:\n"
            "{\n"
            "  \"score\": 0-100,\n"
            "  \"summary\": \"short overall assessment\",\n"
            "  \"warnings\": [\"global issue\"],\n"
            "  \"continuity\": [\"continuity finding\"],\n"
            "  \"monotony\": [\"repetition/diversity finding\"],\n"
            "  \"scene_notes\": [{\"scene_id\": 1, \"severity\": \"info|warning|critical\", \"issue\": \"...\", \"recommendation\": \"...\"}]\n"
            "}\n\n"
            f"Project: {project['title']}\n"
            f"Visual style: {project['visual_style']}\n"
            f"Structured scene intent: {json.dumps(scene_context, ensure_ascii=False)}\n"
            f"Shot Director structural score: {shot_audit.score}; warnings: {json.dumps(shot_audit.warnings, ensure_ascii=False)}"
        )

    @staticmethod
    def _parse_json(text: str) -> dict[str, Any]:
        value = text.strip()
        if value.startswith("```"):
            value = re.sub(r"^```(?:json)?\s*", "", value, flags=re.IGNORECASE)
            value = re.sub(r"\s*```$", "", value)
        try:
            data = json.loads(value)
        except json.JSONDecodeError:
            start = value.find("{")
            end = value.rfind("}")
            if start < 0 or end <= start:
                raise ProviderError("Visual QA provider did not return JSON")
            try:
                data = json.loads(value[start : end + 1])
            except json.JSONDecodeError as exc:
                raise ProviderError("Visual QA provider returned invalid JSON") from exc
        if not isinstance(data, dict):
            raise ProviderError("Visual QA response must be a JSON object")
        return data

    def run(self, project_id: str, provider: VisionProvider) -> tuple[VisualQAReport, Path]:
        project_dir = self._project_dir(project_id)
        qa_dir = project_dir / "qa"
        qa_dir.mkdir(parents=True, exist_ok=True)
        report_path = qa_dir / "visual-qa.json"
        images = self._review_images(project_id)
        scenes = self.store.list_scenes(project_id)
        shot_audit = ShotDirector().audit(scenes)
        self.tasks.set_checkpoint(project_id, "visual_qa", "running")
        try:
            response = provider.analyze_images(
                self._prompt(project_id),
                [str(path) for path in images],
                temperature=0.1,
                max_tokens=2200,
            )
            data = self._parse_json(response.text)
            score = max(0, min(100, int(data.get("score", 0))))
            notes: list[VisualSceneNote] = []
            for item in data.get("scene_notes") or []:
                if not isinstance(item, dict):
                    continue
                try:
                    scene_id = int(item.get("scene_id"))
                except (TypeError, ValueError):
                    continue
                if not 1 <= scene_id <= 8:
                    continue
                severity = str(item.get("severity") or "info").lower()
                if severity not in {"info", "warning", "critical"}:
                    severity = "warning"
                notes.append(
                    VisualSceneNote(
                        scene_id=scene_id,
                        severity=severity,
                        issue=str(item.get("issue") or "").strip(),
                        recommendation=str(item.get("recommendation") or "").strip(),
                    )
                )
            report = VisualQAReport(
                project_id=project_id,
                score=score,
                provider=response.provider,
                model=response.model,
                summary=str(data.get("summary") or "").strip(),
                warnings=[str(item) for item in (data.get("warnings") or [])],
                continuity=[str(item) for item in (data.get("continuity") or [])],
                monotony=[str(item) for item in (data.get("monotony") or [])],
                scene_notes=notes,
                shot_director_score=shot_audit.score,
                shot_director_warnings=shot_audit.warnings,
                raw_text=response.text,
            )
            atomic_write_json(report_path, report.to_dict())
            self.tasks.set_checkpoint(
                project_id,
                "visual_qa",
                "done",
                artifact_path=report_path,
                metadata={
                    "score": score,
                    "provider": response.provider,
                    "model": response.model,
                    "scene_notes": len(notes),
                },
            )
            return report, report_path
        except Exception as exc:
            self.tasks.set_checkpoint(
                project_id,
                "visual_qa",
                "failed",
                metadata={"error": f"{type(exc).__name__}: {exc}"},
            )
            raise


def main() -> None:
    parser = argparse.ArgumentParser(description="Run CSP Visual QA through the configured VisionProvider")
    parser.add_argument("project_id")
    parser.add_argument("--provider", default=os.getenv("CSP_AI_PROVIDER", "nvidia_nim"))
    args = parser.parse_args()

    with StudioStore(DB_PATH) as store:
        provider = get_provider(args.provider)
        try:
            report, path = VisualQA(store).run(args.project_id, provider)
        finally:
            close = getattr(provider, "close", None)
            if callable(close):
                close()
        print(f"VISUAL QA: {report.score}/100")
        print(f"PROVIDER: {report.provider} / {report.model}")
        print(f"REPORT: {path}")
        for warning in report.warnings:
            print(f"WARN: {warning}")
        for note in report.scene_notes:
            if note.severity != "info":
                print(f"SCENE {note.scene_id:02d} [{note.severity.upper()}]: {note.issue} -> {note.recommendation}")


if __name__ == "__main__":
    main()
