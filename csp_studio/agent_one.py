from __future__ import annotations

import argparse
import json
import os
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .asset_manager import AssetManager
from .pipeline_state import is_stale
from .providers.base import ChatProvider, ProviderError
from .store import StudioStore
from .task_engine import TaskEngine

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_ROOT = Path(os.getenv("CSP_OUTPUT_DIR", str(ROOT / "output"))).expanduser().resolve()
DB_PATH = Path(os.getenv("CSP_STUDIO_DB", str(OUTPUT_ROOT / "csp-studio.db"))).expanduser().resolve()


def _slug(value: str) -> str:
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-zA-Z0-9]+", "-", ascii_value).strip("-").lower() or "project"


@dataclass(slots=True)
class ReadinessCheck:
    key: str
    ok: bool
    label: str
    detail: str = ""
    blocking: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class AgentOneReport:
    project_id: str
    title: str
    checks: list[ReadinessCheck] = field(default_factory=list)
    next_action: str = "none"
    next_action_detail: str = ""
    stage: str = "draft"
    assets_ready: bool = False
    production_ready: bool = False
    final_ready: bool = False

    @property
    def blockers(self) -> list[ReadinessCheck]:
        return [check for check in self.checks if check.blocking and not check.ok]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["blockers"] = [item.to_dict() for item in self.blockers]
        return data


class AgentOne:
    """CSP production operator.

    Readiness is deterministic. An LLM/NIM can explain or prioritize the verified
    state, but it is never allowed to turn a failed deterministic gate into READY.
    """

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

    def inspect(self, project_id: str) -> AgentOneReport:
        project = self._project_row(project_id)
        project_dir = self._project_dir(project_id)
        scenes = self.store.list_scenes(project_id)
        checks: list[ReadinessCheck] = []

        exact_scene_count = len(scenes) == 8
        checks.append(ReadinessCheck("scene_count", exact_scene_count, "8 scen projektu", f"Znaleziono {len(scenes)} scen; CSP Short wymaga dokładnie 8."))

        missing_assets: list[int] = []
        missing_files: list[int] = []
        review_pending: list[int] = []
        for scene in scenes:
            active = self.assets.active_asset(project_id, scene.scene_id, "image")
            if active is None:
                missing_assets.append(scene.scene_id)
            elif not Path(active.path).is_file():
                missing_files.append(scene.scene_id)
            if scene.status not in {"approved", "render_ready"}:
                review_pending.append(scene.scene_id)

        images_ok = exact_scene_count and not missing_assets and not missing_files
        image_detail_parts = []
        if missing_assets:
            image_detail_parts.append("brak aktywnego assetu: " + ", ".join(map(str, missing_assets)))
        if missing_files:
            image_detail_parts.append("brak pliku na dysku: " + ", ".join(map(str, missing_files)))
        checks.append(ReadinessCheck("active_images", images_ok, "Aktywne obrazy scen", "; ".join(image_detail_parts) if image_detail_parts else "8/8 aktywnych obrazów istnieje na dysku."))

        review_ok = exact_scene_count and not review_pending
        checks.append(ReadinessCheck("scene_review", review_ok, "Review scen", "Wymagają review: " + ", ".join(map(str, review_pending)) if review_pending else "8/8 scen zatwierdzonych."))

        audio_dir = project_dir / "audio"
        voice = audio_dir / "voice.wav"
        timings = audio_dir / "tts-timings.json"
        tts_ok = voice.is_file() and timings.is_file() and self._timings_cover_scenes(timings, scenes)
        checks.append(ReadinessCheck("tts", tts_ok, "Narrator + dokładne timingi", "voice.wav i kompletne tts-timings.json są gotowe." if tts_ok else "Brakuje voice.wav lub kompletnych timingów 8 scen."))

        ass = project_dir / "subtitles.ass"
        srt = project_dir / "subtitles.srt"
        captions_stale = is_stale(self.tasks, project_id, "captions")
        captions_ok = (ass.is_file() or srt.is_file()) and not captions_stale
        checks.append(ReadinessCheck("captions", captions_ok, "Napisy", "Napisy ASS/SRT gotowe." if captions_ok else ("Napisy są nieaktualne po zmianie TTS." if captions_stale else "Brak subtitles.ass i subtitles.srt.")))

        final_mix = audio_dir / "final_mix.wav"
        sound_stale = is_stale(self.tasks, project_id, "sound_design")
        sound_ok = final_mix.is_file() and not sound_stale
        checks.append(ReadinessCheck("sound_design", sound_ok, "Finalny miks audio", "final_mix.wav gotowy." if sound_ok else ("Miks audio jest nieaktualny po zmianie TTS." if sound_stale else "Brak audio/final_mix.wav.")))

        visual_checkpoint = self.tasks.get_checkpoint(project_id, "visual_qa")
        visual_qa_ok = bool(visual_checkpoint and visual_checkpoint["state"] == "done")
        checks.append(ReadinessCheck("visual_qa", visual_qa_ok, "Visual QA", "Visual QA zakończone." if visual_qa_ok else "Visual QA jeszcze nie zostało wykonane lub jest nieaktualne.", blocking=False))

        opencut_manifest = project_dir / "opencut" / "csp-opencut.json"
        opencut_stale = is_stale(self.tasks, project_id, "opencut_export")
        opencut_ok = opencut_manifest.is_file() and not opencut_stale
        checks.append(ReadinessCheck("opencut_export", opencut_ok, "OpenCut interchange", "Manifest OpenCut gotowy." if opencut_ok else ("Manifest OpenCut jest nieaktualny." if opencut_stale else "Brak opencut/csp-opencut.json."), blocking=False))

        final_mp4 = project_dir / "final.mp4"
        render_stale = is_stale(self.tasks, project_id, "render_final")
        final_exists = final_mp4.is_file() and final_mp4.stat().st_size > 0 and not render_stale
        checks.append(ReadinessCheck("final_render", final_exists, "Final MP4", "final.mp4 istnieje i jest aktualny." if final_exists else ("final.mp4 istnieje, ale jest nieaktualny." if render_stale and final_mp4.is_file() else "Finalny render nie istnieje jeszcze."), blocking=False))

        assets_ready = exact_scene_count and images_ok
        production_ready = assets_ready and tts_ok and captions_ok and sound_ok
        final_ready = production_ready and review_ok
        next_action, detail = self._next_action(
            exact_scene_count=exact_scene_count,
            images_ok=images_ok,
            missing_assets=missing_assets,
            missing_files=missing_files,
            review_pending=review_pending,
            tts_ok=tts_ok,
            captions_ok=captions_ok,
            sound_ok=sound_ok,
            visual_qa_ok=visual_qa_ok,
            opencut_ok=opencut_ok,
            final_exists=final_exists,
        )

        if final_exists:
            stage = "rendered"
        elif final_ready:
            stage = "render_ready"
        elif production_ready:
            stage = "review"
        elif assets_ready:
            stage = "production"
        else:
            stage = "assets"

        return AgentOneReport(project_id=project_id, title=project["title"], checks=checks, next_action=next_action, next_action_detail=detail, stage=stage, assets_ready=assets_ready, production_ready=production_ready, final_ready=final_ready)

    @staticmethod
    def _timings_cover_scenes(path: Path, scenes) -> bool:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            rows = data.get("scenes") or []
            expected = [scene.scene_id for scene in scenes]
            actual = [int(item["id"]) for item in rows]
            return len(expected) == 8 and actual == expected and all(float(item.get("duration", 0)) > 0 for item in rows)
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            return False

    @staticmethod
    def _next_action(*, exact_scene_count: bool, images_ok: bool, missing_assets: list[int], missing_files: list[int], review_pending: list[int], tts_ok: bool, captions_ok: bool, sound_ok: bool, visual_qa_ok: bool, opencut_ok: bool, final_exists: bool) -> tuple[str, str]:
        if not exact_scene_count:
            return "fix_scene_plan", "Projekt musi mieć dokładnie 8 scen."
        if not images_ok:
            scenes = sorted(set(missing_assets + missing_files))
            return "complete_images", "Uzupełnij aktywne obrazy scen: " + ", ".join(map(str, scenes))
        if not tts_ok:
            return "generate_tts", "Wygeneruj narratora i dokładne timingi scen."
        if not captions_ok:
            return "generate_captions", "Wygeneruj napisy z timestampów Whisper."
        if not sound_ok:
            return "sound_design", "Zbuduj final_mix.wav."
        if not visual_qa_ok:
            return "visual_qa", "Uruchom Visual QA przed finalnym review; gate jest doradczy, nie blokujący."
        if review_pending:
            return "review_scenes", "Zatwierdź sceny: " + ", ".join(map(str, review_pending))
        if not opencut_ok:
            return "export_opencut", "Wyeksportuj aktualny projekt do kontraktu OpenCut."
        if not final_exists:
            return "render_final", "Projekt przeszedł bramki. Uruchom finalny render."
        return "publish_review", "Final MP4 istnieje; przejdź do kontroli publikacji."

    def explain(self, project_id: str, provider: ChatProvider) -> dict[str, Any]:
        report = self.inspect(project_id)
        safe_state = report.to_dict()
        prompt = (
            "Jesteś Agent One dla fikcyjnego kanału Ciemna Strona Polski. "
            "Na podstawie WYŁĄCZNIE poniższego zweryfikowanego stanu produkcji napisz krótki raport po polsku: "
            "co jest gotowe, co blokuje produkcję i jaki jest najbliższy sensowny krok. "
            "Nie zmieniaj wartości readiness i nie twierdź, że plik istnieje, jeśli stan mówi inaczej.\n\n"
            + json.dumps(safe_state, ensure_ascii=False, indent=2)
        )
        response = provider.chat(
            [
                {"role": "system", "content": "Jesteś operatorem produkcyjnym CSP. Fakty dostarcza deterministyczny readiness checker."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            max_tokens=800,
        )
        return {"report": safe_state, "assistant": {"provider": response.provider, "model": response.model, "text": response.text, "usage": response.usage}}

    def enqueue_next(self, project_id: str) -> dict[str, Any]:
        report = self.inspect(project_id)
        mapping = {
            "generate_tts": ("tts", "gpu"),
            "generate_captions": ("captions", "gpu"),
            "sound_design": ("sound_design", "cpu"),
            "visual_qa": ("visual_qa", "network"),
            "export_opencut": ("opencut_export", "io"),
            "render_final": ("render_final", "gpu"),
        }
        mapped = mapping.get(report.next_action)
        if mapped is None:
            return {"queued": False, "reason": report.next_action, "report": report.to_dict()}
        stage, resource = mapped
        existing = [task for task in self.tasks.list(project_id) if task.stage == stage and task.state in {"queued", "running"}]
        if existing:
            return {"queued": False, "reason": "already_queued", "task": existing[0].to_dict(), "report": report.to_dict()}
        task = self.tasks.submit(project_id, stage, resource=resource, payload={"source": "agent_one", "readiness_action": report.next_action})
        return {"queued": True, "task": task.to_dict(), "report": report.to_dict()}


def main() -> None:
    parser = argparse.ArgumentParser(description="CSP Studio Agent One readiness operator")
    parser.add_argument("project_id")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    with StudioStore(DB_PATH) as store:
        report = AgentOne(store).inspect(args.project_id)
        if args.json:
            print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
            return
        print(f"AGENT ONE: {report.project_id} — {report.title}")
        print(f"STAGE: {report.stage}")
        for check in report.checks:
            icon = "OK" if check.ok else ("WARN" if not check.blocking else "BLOCK")
            print(f"[{icon:5s}] {check.label}: {check.detail}")
        print(f"NEXT: {report.next_action} — {report.next_action_detail}")


if __name__ == "__main__":
    main()
