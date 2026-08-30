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
SCENE_IDS = tuple(range(1, 9))
PLACEHOLDER_NOTE_PAIRS = {
    ("specific issue", "specific fix"),
    ("issue", "recommendation"),
}


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
    strategy: str = "single_scene_prose_v2"
    aggregate_status: str = "complete"

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

    def _review_images(self, project_id: str) -> dict[int, Path]:
        scenes = self.store.list_scenes(project_id)
        if len(scenes) != 8:
            raise RuntimeError(f"Visual QA requires exactly 8 scenes, got {len(scenes)}")
        qa_dir = self._project_dir(project_id) / "qa" / "thumbnails"
        qa_dir.mkdir(parents=True, exist_ok=True)
        output: dict[int, Path] = {}
        for scene in scenes:
            asset = self.assets.active_asset(project_id, scene.scene_id, "image")
            if asset is None:
                raise RuntimeError(f"Scene {scene.scene_id:02d} has no active image")
            source = Path(asset.path)
            if not source.is_file():
                raise FileNotFoundError(source)
            target = qa_dir / f"scene-{scene.scene_id:02d}-r{asset.revision}.jpg"
            if not target.is_file():
                with Image.open(source) as image:
                    converted = image.convert("RGB")
                    converted.thumbnail((360, 640), Image.Resampling.LANCZOS)
                    converted.save(target, "JPEG", quality=76, optimize=True)
            output[scene.scene_id] = target
        return output

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
                preview = value[:300].replace("\n", " ")
                raise ProviderError(f"Visual QA aggregator did not return JSON. Response starts: {preview!r}")
            try:
                data = json.loads(value[start : end + 1])
            except json.JSONDecodeError as exc:
                raise ProviderError("Visual QA aggregator returned invalid JSON") from exc
        if not isinstance(data, dict):
            raise ProviderError("Visual QA aggregate response must be a JSON object")
        return data

    @staticmethod
    def _validate_aggregate(data: dict[str, Any]) -> dict[str, Any]:
        required = {"score", "summary", "warnings", "continuity", "monotony", "scene_notes"}
        missing = sorted(required - set(data))
        if missing:
            raise ProviderError(f"Visual QA aggregate is missing required fields: {', '.join(missing)}")

        score = data.get("score")
        if isinstance(score, bool):
            raise ProviderError("Visual QA aggregate score must be a number, not boolean")
        try:
            numeric_score = int(score)
        except (TypeError, ValueError) as exc:
            raise ProviderError(f"Visual QA aggregate score is invalid: {score!r}") from exc
        if not 0 <= numeric_score <= 100:
            raise ProviderError(f"Visual QA aggregate score must be 0-100, got {numeric_score}")

        if not isinstance(data.get("summary"), str):
            raise ProviderError("Visual QA aggregate summary must be text")
        for key in ("warnings", "continuity", "monotony", "scene_notes"):
            if not isinstance(data.get(key), list):
                raise ProviderError(f"Visual QA aggregate {key} must be a list")

        normalized = dict(data)
        normalized["score"] = numeric_score
        return normalized

    @staticmethod
    def _normalize_notes(data: dict[str, Any]) -> list[VisualSceneNote]:
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
            issue = str(item.get("issue") or "").strip()
            recommendation = str(item.get("recommendation") or "").strip()
            if not issue and not recommendation:
                continue
            if (issue.lower(), recommendation.lower()) in PLACEHOLDER_NOTE_PAIRS:
                continue
            severity = str(item.get("severity") or "info").lower()
            if severity not in {"info", "warning", "critical"}:
                severity = "warning"
            notes.append(
                VisualSceneNote(
                    scene_id=scene_id,
                    severity=severity,
                    issue=issue,
                    recommendation=recommendation,
                )
            )
        return notes

    def _scene_prompt(self, project_id: str, scene_id: int) -> str:
        project = self._project_row(project_id)
        scene = next(scene for scene in self.store.list_scenes(project_id) if scene.scene_id == scene_id)
        context = {
            "scene_id": scene.scene_id,
            "narration": scene.text,
            "shot": scene.shot.to_dict(),
            "motion": scene.motion,
            "continuity_refs": scene.continuity_refs,
        }
        return (
            "You are Visual Director QA for a FICTIONAL Polish documentary-thriller YouTube Short. "
            f"Review ONLY the attached frame for Scene {scene_id}. Evaluate the actual image. "
            "Write a concise factual review in plain text, maximum 120 words. Cover: framing/camera angle, dominant subject, location cues, "
            "documentary realism, obvious AI-looking faces/hands/anatomy, 9:16 phone readability, and whether the image supports the narration. "
            "Mention any recurring visual element that could matter for continuity. If something is fine, say so briefly. "
            "Do not output JSON or markdown and do not compare against unseen scenes.\n"
            f"Project: {project['title']}\nVisual style: {project['visual_style']}\nScene context: {json.dumps(context, ensure_ascii=False)}"
        )

    def _scene_path(self, project_id: str, scene_id: int) -> Path:
        path = self._project_dir(project_id) / "qa" / "scenes" / f"scene-{scene_id:02d}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def _run_scene(self, project_id: str, provider: VisionProvider, images: dict[int, Path], scene_id: int) -> dict[str, Any]:
        stage = f"visual_qa_scene_{scene_id:02d}"
        artifact = self._scene_path(project_id, scene_id)
        checkpoint = self.tasks.get_checkpoint(project_id, stage)
        if checkpoint and checkpoint["state"] == "done" and artifact.is_file():
            try:
                cached = json.loads(artifact.read_text(encoding="utf-8"))
                if (
                    isinstance(cached, dict)
                    and int(cached.get("scene_id", -1)) == scene_id
                    and str(cached.get("review_text") or "").strip()
                ):
                    print(f"VISUAL QA SCENE {scene_id:02d}: RESUME")
                    return cached
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                pass

        self.tasks.set_checkpoint(project_id, stage, "running")
        print(f"VISUAL QA SCENE {scene_id:02d}: ANALYZE")
        try:
            response = provider.analyze_images(
                self._scene_prompt(project_id, scene_id),
                [str(images[scene_id])],
                temperature=0.1,
                max_tokens=420,
            )
            review_text = str(response.text or "").strip()
            if not review_text:
                raise ProviderError(
                    f"Vision provider returned empty text for Scene {scene_id:02d}; model={response.model}"
                )
            payload = {
                "scene_id": scene_id,
                "review_text": review_text,
                "provider": response.provider,
                "model": response.model,
            }
            atomic_write_json(artifact, payload)
            self.tasks.set_checkpoint(
                project_id,
                stage,
                "done",
                artifact_path=artifact,
                metadata={"provider": response.provider, "model": response.model, "chars": len(review_text)},
            )
            return payload
        except Exception as exc:
            self.tasks.set_checkpoint(project_id, stage, "failed", metadata={"error": f"{type(exc).__name__}: {exc}"})
            raise

    def _aggregate_prompt(self, project_id: str, scene_results: list[dict[str, Any]], shot_score: int, shot_warnings: list[str]) -> str:
        project = self._project_row(project_id)
        compact = [{"scene_id": item["scene_id"], "review_text": item["review_text"]} for item in scene_results]
        return (
            "You are the final Visual Director QA aggregator for a FICTIONAL Polish documentary-thriller Short. "
            "You are NOT viewing images now. Use only the eight verified single-scene visual reviews and Shot Director findings below. "
            "Compare descriptions to find repeated adjacent framing/subjects, continuity consistency, and scenes needing correction. "
            "Do not invent visual facts. Return ONLY valid JSON, no markdown. "
            "All six top-level keys are REQUIRED and score MUST be an integer from 0 to 100. "
            "Do not emit placeholder or empty scene_notes; omit a scene note unless issue or recommendation contains useful text. "
            "Exact shape:\n"
            "{\"score\":75,\"summary\":\"short assessment\",\"warnings\":[],\"continuity\":[],\"monotony\":[],\"scene_notes\":[]}\n"
            f"Project: {project['title']}\nScene reviews: {json.dumps(compact, ensure_ascii=False)}\n"
            f"Shot Director score: {shot_score}; warnings: {json.dumps(shot_warnings, ensure_ascii=False)}"
        )

    @staticmethod
    def _fallback_aggregate(scene_results: list[dict[str, Any]], shot_score: int, reason: str | None = None) -> dict[str, Any]:
        summary = "Visual scene reviews completed. Structured aggregate unavailable; inspect per-scene review_text artifacts."
        warnings: list[str] = []
        if reason:
            warnings.append(f"Text aggregation unavailable: {reason[:300]}")
        return {
            "score": max(0, min(100, int(shot_score))),
            "summary": summary,
            "warnings": warnings,
            "continuity": [],
            "monotony": [],
            "scene_notes": [],
        }

    @staticmethod
    def _aggregate_debug_path(project_dir: Path) -> Path:
        return project_dir / "qa" / "aggregate-response.json"

    def run(self, project_id: str, provider: VisionProvider) -> tuple[VisualQAReport, Path]:
        project_dir = self._project_dir(project_id)
        qa_dir = project_dir / "qa"
        qa_dir.mkdir(parents=True, exist_ok=True)
        report_path = qa_dir / "visual-qa.json"
        aggregate_debug_path = self._aggregate_debug_path(project_dir)
        images = self._review_images(project_id)
        scenes = self.store.list_scenes(project_id)
        shot_audit = ShotDirector().audit(scenes)
        self.tasks.set_checkpoint(project_id, "visual_qa", "running")
        aggregate_status = "fallback"
        aggregate_response = None
        try:
            scene_results = [self._run_scene(project_id, provider, images, scene_id) for scene_id in SCENE_IDS]

            chat = getattr(provider, "chat", None)
            if callable(chat):
                print("VISUAL QA: AGGREGATE TEXT")
                try:
                    aggregate_response = chat(
                        [{"role": "user", "content": self._aggregate_prompt(project_id, scene_results, shot_audit.score, shot_audit.warnings)}],
                        temperature=0.1,
                        max_tokens=1200,
                    )
                    atomic_write_json(
                        aggregate_debug_path,
                        {
                            "provider": aggregate_response.provider,
                            "model": aggregate_response.model,
                            "text": aggregate_response.text,
                        },
                    )
                    data = self._validate_aggregate(self._parse_json(aggregate_response.text))
                    aggregate_status = "complete"
                except Exception as exc:
                    print(f"VISUAL QA: AGGREGATE FALLBACK ({exc})")
                    if aggregate_response is None:
                        atomic_write_json(
                            aggregate_debug_path,
                            {"provider": getattr(provider, "name", "unknown"), "error": f"{type(exc).__name__}: {exc}"},
                        )
                    data = self._fallback_aggregate(scene_results, shot_audit.score, str(exc))
            else:
                data = self._fallback_aggregate(scene_results, shot_audit.score, "Provider has no chat aggregation capability")

            score = int(data["score"])
            notes = self._normalize_notes(data)
            first_provider = scene_results[0].get("provider", getattr(provider, "name", "unknown"))
            first_model = scene_results[0].get("model", "unknown")
            report = VisualQAReport(
                project_id=project_id,
                score=score,
                provider=str(first_provider),
                model=str(first_model),
                summary=str(data.get("summary") or "").strip(),
                warnings=[str(item) for item in (data.get("warnings") or [])],
                continuity=[str(item) for item in (data.get("continuity") or [])],
                monotony=[str(item) for item in (data.get("monotony") or [])],
                scene_notes=notes,
                shot_director_score=shot_audit.score,
                shot_director_warnings=shot_audit.warnings,
                raw_text=aggregate_response.text if aggregate_response is not None else json.dumps(data, ensure_ascii=False),
                strategy="single_scene_prose_v2",
                aggregate_status=aggregate_status,
            )
            atomic_write_json(report_path, report.to_dict())
            self.tasks.set_checkpoint(
                project_id,
                "visual_qa",
                "done",
                artifact_path=report_path,
                metadata={
                    "score": score,
                    "provider": report.provider,
                    "model": report.model,
                    "scene_notes": len(notes),
                    "strategy": report.strategy,
                    "aggregate_status": aggregate_status,
                    "scenes": len(scene_results),
                },
            )
            return report, report_path
        except Exception as exc:
            self.tasks.set_checkpoint(
                project_id,
                "visual_qa",
                "failed",
                metadata={"error": f"{type(exc).__name__}: {exc}", "strategy": "single_scene_prose_v2"},
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
        print(f"STRATEGY: {report.strategy}")
        print(f"AGGREGATE: {report.aggregate_status.upper()}")
        print(f"REPORT: {path}")
        for warning in report.warnings:
            print(f"WARN: {warning}")
        for note in report.scene_notes:
            if note.severity != "info":
                print(f"SCENE {note.scene_id:02d} [{note.severity.upper()}]: {note.issue} -> {note.recommendation}")


if __name__ == "__main__":
    main()
