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
from .providers import ChatProvider, VisionProvider, get_provider
from .providers.base import ProviderError
from .shot_director import ShotDirector
from .store import StudioStore
from .task_engine import TaskEngine, atomic_write_json

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_ROOT = Path(os.getenv("CSP_OUTPUT_DIR", str(ROOT / "output"))).expanduser().resolve()
DB_PATH = Path(os.getenv("CSP_STUDIO_DB", str(OUTPUT_ROOT / "csp-studio.db"))).expanduser().resolve()
PAIR_IDS = ((1, 2), (3, 4), (5, 6), (7, 8))


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
    strategy: str = "pairwise_v1"

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
                    converted.save(target, "JPEG", quality=78, optimize=True)
            output[scene.scene_id] = target
        return output

    def _pair_prompt(self, project_id: str, first_id: int, second_id: int) -> str:
        project = self._project_row(project_id)
        scenes = {scene.scene_id: scene for scene in self.store.list_scenes(project_id)}
        context = []
        for scene_id in (first_id, second_id):
            scene = scenes[scene_id]
            context.append(
                {
                    "scene_id": scene.scene_id,
                    "narration": scene.text,
                    "shot": scene.shot.to_dict(),
                    "motion": scene.motion,
                    "continuity_refs": scene.continuity_refs,
                }
            )
        return (
            "You are Visual Director QA for a FICTIONAL Polish documentary-thriller YouTube Short. "
            f"The two attached frames are Scene {first_id} then Scene {second_id}. Evaluate the actual images and compare them. "
            "Focus on framing diversity, repeated visual language, continuity, documentary realism, AI-looking faces/hands/anatomy, "
            "9:16 phone readability, and whether each image supports its narration. Do not invent facts outside the frames/context. "
            "Return ONLY valid JSON, no markdown, in this exact shape:\n"
            "{\"pair_score\":0-100,\"warnings\":[\"...\"],\"continuity\":[\"...\"],"
            "\"monotony\":[\"...\"],\"scene_notes\":[{\"scene_id\":1,\"severity\":\"info|warning|critical\","
            "\"issue\":\"...\",\"recommendation\":\"...\"}]}\n"
            f"Project: {project['title']}\nVisual style: {project['visual_style']}\n"
            f"Scene context: {json.dumps(context, ensure_ascii=False)}"
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
        return notes

    def _pair_path(self, project_id: str, first_id: int, second_id: int) -> Path:
        path = self._project_dir(project_id) / "qa" / "pairs" / f"pair-{first_id:02d}-{second_id:02d}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def _run_pair(
        self,
        project_id: str,
        provider: VisionProvider,
        images: dict[int, Path],
        first_id: int,
        second_id: int,
    ) -> dict[str, Any]:
        stage = f"visual_qa_pair_{first_id:02d}_{second_id:02d}"
        artifact = self._pair_path(project_id, first_id, second_id)
        checkpoint = self.tasks.get_checkpoint(project_id, stage)
        if checkpoint and checkpoint["state"] == "done" and artifact.is_file():
            try:
                cached = json.loads(artifact.read_text(encoding="utf-8"))
                if isinstance(cached, dict) and cached.get("pair") == [first_id, second_id]:
                    print(f"VISUAL QA PAIR {first_id:02d}-{second_id:02d}: RESUME")
                    return cached
            except (OSError, json.JSONDecodeError):
                pass

        self.tasks.set_checkpoint(project_id, stage, "running")
        print(f"VISUAL QA PAIR {first_id:02d}-{second_id:02d}: ANALYZE")
        try:
            response = provider.analyze_images(
                self._pair_prompt(project_id, first_id, second_id),
                [str(images[first_id]), str(images[second_id])],
                temperature=0.1,
                max_tokens=900,
            )
            data = self._parse_json(response.text)
            payload = {
                "pair": [first_id, second_id],
                "pair_score": max(0, min(100, int(data.get("pair_score", 0)))),
                "warnings": [str(x) for x in (data.get("warnings") or [])],
                "continuity": [str(x) for x in (data.get("continuity") or [])],
                "monotony": [str(x) for x in (data.get("monotony") or [])],
                "scene_notes": [note.to_dict() for note in self._normalize_notes(data)],
                "provider": response.provider,
                "model": response.model,
            }
            atomic_write_json(artifact, payload)
            self.tasks.set_checkpoint(
                project_id,
                stage,
                "done",
                artifact_path=artifact,
                metadata={"pair_score": payload["pair_score"], "provider": response.provider, "model": response.model},
            )
            return payload
        except Exception as exc:
            self.tasks.set_checkpoint(
                project_id,
                stage,
                "failed",
                metadata={"error": f"{type(exc).__name__}: {exc}"},
            )
            raise

    def _aggregate_prompt(self, project_id: str, pair_results: list[dict[str, Any]], shot_score: int, shot_warnings: list[str]) -> str:
        project = self._project_row(project_id)
        return (
            "You are the final Visual Director QA aggregator for a FICTIONAL Polish documentary-thriller Short. "
            "You are NOT viewing images now. Summarize only the four verified pair-review JSON objects plus Shot Director findings below. "
            "Do not invent new visual observations. Merge duplicate findings, preserve scene IDs, and return ONLY valid JSON in this shape:\n"
            "{\"score\":0-100,\"summary\":\"...\",\"warnings\":[\"...\"],\"continuity\":[\"...\"],"
            "\"monotony\":[\"...\"],\"scene_notes\":[{\"scene_id\":1,\"severity\":\"info|warning|critical\","
            "\"issue\":\"...\",\"recommendation\":\"...\"}]}\n"
            f"Project: {project['title']}\nPair reviews: {json.dumps(pair_results, ensure_ascii=False)}\n"
            f"Shot Director score: {shot_score}; warnings: {json.dumps(shot_warnings, ensure_ascii=False)}"
        )

    def _local_aggregate(self, pair_results: list[dict[str, Any]], shot_score: int) -> dict[str, Any]:
        scores = [int(item.get("pair_score", 0)) for item in pair_results]
        pair_average = round(sum(scores) / len(scores)) if scores else 0
        score = round(pair_average * 0.8 + shot_score * 0.2)
        return {
            "score": max(0, min(100, score)),
            "summary": "Pairwise Visual QA completed; final score combines pair reviews with Shot Director structure.",
            "warnings": list(dict.fromkeys(x for item in pair_results for x in item.get("warnings", []))),
            "continuity": list(dict.fromkeys(x for item in pair_results for x in item.get("continuity", []))),
            "monotony": list(dict.fromkeys(x for item in pair_results for x in item.get("monotony", []))),
            "scene_notes": [note for item in pair_results for note in item.get("scene_notes", [])],
        }

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
            pair_results = [
                self._run_pair(project_id, provider, images, first_id, second_id)
                for first_id, second_id in PAIR_IDS
            ]

            aggregate_response = None
            chat = getattr(provider, "chat", None)
            if callable(chat):
                print("VISUAL QA: AGGREGATE TEXT")
                aggregate_response = chat(
                    [{"role": "user", "content": self._aggregate_prompt(project_id, pair_results, shot_audit.score, shot_audit.warnings)}],
                    temperature=0.1,
                    max_tokens=1400,
                )
                data = self._parse_json(aggregate_response.text)
            else:
                data = self._local_aggregate(pair_results, shot_audit.score)

            score = max(0, min(100, int(data.get("score", 0))))
            notes = self._normalize_notes(data)
            first_provider = pair_results[0].get("provider", getattr(provider, "name", "unknown"))
            first_model = pair_results[0].get("model", "unknown")
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
                strategy="pairwise_v1",
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
                    "pairs": len(pair_results),
                },
            )
            return report, report_path
        except Exception as exc:
            self.tasks.set_checkpoint(
                project_id,
                "visual_qa",
                "failed",
                metadata={"error": f"{type(exc).__name__}: {exc}", "strategy": "pairwise_v1"},
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
        print(f"STRATEGY: {report.strategy}")
        print(f"PROVIDER: {report.provider} / {report.model}")
        print(f"REPORT: {path}")
        for warning in report.warnings:
            print(f"WARN: {warning}")
        for note in report.scene_notes:
            if note.severity != "info":
                print(f"SCENE {note.scene_id:02d} [{note.severity.upper()}]: {note.issue} -> {note.recommendation}")


if __name__ == "__main__":
    main()
