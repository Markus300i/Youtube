from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .import_short import project_from_short
from .new_short_wizard import NewShortWizard, WizardValidationError, normalize_wizard_payload
from .providers.base import ChatProvider
from .shot_director import ShotDirector
from .store import StudioStore
from .visual_bible import VALID_KINDS, VisualBible, VisualBibleEntity

ENTITY_KEY_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}$")


class WizardV2Error(ValueError):
    pass


def _slug(value: str) -> str:
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_value).strip("-").lower()
    return slug[:64] or "new-short"


def _extract_json_object(text: str) -> dict[str, Any]:
    value = str(text or "").strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*", "", value, flags=re.IGNORECASE)
        value = re.sub(r"\s*```$", "", value)
    start = value.find("{")
    end = value.rfind("}")
    if start < 0 or end <= start:
        raise WizardV2Error("AI draft did not contain a JSON object")
    try:
        payload = json.loads(value[start : end + 1])
    except json.JSONDecodeError as exc:
        raise WizardV2Error(f"AI draft returned invalid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise WizardV2Error("AI draft must be a JSON object")
    return payload


def _validate_visual_bible(payload: dict[str, Any]) -> dict[str, Any]:
    raw = payload.get("visual_bible") or {}
    if not isinstance(raw, dict):
        raise WizardV2Error("visual_bible must be an object")
    entities_raw = raw.get("entities") or []
    assignments_raw = raw.get("assignments") or {}
    if not isinstance(entities_raw, list):
        raise WizardV2Error("visual_bible.entities must be a list")
    if not isinstance(assignments_raw, dict):
        raise WizardV2Error("visual_bible.assignments must be an object")
    if len(entities_raw) > 24:
        raise WizardV2Error("visual_bible supports at most 24 starter entities")

    entities: list[dict[str, Any]] = []
    keys: set[str] = set()
    for raw_entity in entities_raw:
        if not isinstance(raw_entity, dict):
            raise WizardV2Error("each Visual Bible entity must be an object")
        key = str(raw_entity.get("entity_key") or "").strip()
        kind = str(raw_entity.get("kind") or "").strip()
        name = str(raw_entity.get("name") or "").strip()
        if not ENTITY_KEY_RE.fullmatch(key):
            raise WizardV2Error(f"invalid Visual Bible entity_key: {key}")
        if key in keys:
            raise WizardV2Error(f"duplicate Visual Bible entity_key: {key}")
        if kind not in VALID_KINDS:
            raise WizardV2Error(f"unsupported Visual Bible kind: {kind}")
        if not name:
            raise WizardV2Error(f"Visual Bible entity {key}: name is required")
        keys.add(key)
        entities.append(
            {
                "entity_key": key,
                "kind": kind,
                "name": name,
                "description": str(raw_entity.get("description") or "").strip(),
                "prompt_fragment": str(raw_entity.get("prompt_fragment") or "").strip(),
                "reference_asset_path": None,
                "metadata": dict(raw_entity.get("metadata") or {}),
                "active": bool(raw_entity.get("active", True)),
            }
        )

    assignments: dict[str, list[str]] = {}
    for scene_key, raw_keys in assignments_raw.items():
        try:
            scene_id = int(scene_key)
        except (TypeError, ValueError) as exc:
            raise WizardV2Error(f"invalid Visual Bible scene id: {scene_key}") from exc
        if scene_id not in range(1, 9):
            raise WizardV2Error(f"Visual Bible assignment scene must be 1..8, got {scene_id}")
        if not isinstance(raw_keys, list):
            raise WizardV2Error(f"Visual Bible assignment for scene {scene_id} must be a list")
        unique = list(dict.fromkeys(str(item).strip() for item in raw_keys if str(item).strip()))
        missing = [item for item in unique if item not in keys]
        if missing:
            raise WizardV2Error(f"scene {scene_id} references unknown Visual Bible entities: {', '.join(missing)}")
        assignments[str(scene_id)] = unique

    return {"entities": entities, "assignments": assignments}


@dataclass(slots=True)
class WizardV2Draft:
    draft: dict[str, Any]
    visual_bible: dict[str, Any]
    shot_audit: dict[str, Any]
    provider: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "draft": self.draft,
            "visual_bible": self.visual_bible,
            "shot_audit": self.shot_audit,
            "provider": self.provider,
        }


class WizardV2:
    """AI-assisted draft layer in front of the deterministic V1 creation gate."""

    def __init__(self, provider: ChatProvider):
        self.provider = provider

    def draft(self, topic: str, *, project_id: str | None = None, title: str | None = None) -> WizardV2Draft:
        topic = str(topic or "").strip()
        if not topic:
            raise WizardV2Error("topic is required")
        requested_id = str(project_id or "").strip()
        requested_title = str(title or "").strip()
        system = (
            "Tworzysz WYŁĄCZNIE oryginalne, fikcyjne YouTube Shorts dla kanału Ciemna Strona Polski. "
            "Akcja ma być osadzona we współczesnych polskich realiach i mieć ton spokojnego thrillera dokumentalnego. "
            "Nie przedstawiaj fikcji jako prawdziwego wydarzenia, nie używaj prawdziwych ofiar ani nierozwiązanych spraw jako faktów. "
            "Zwróć wyłącznie poprawny JSON, bez markdownu, bez analizy i bez komentarzy."
        )
        prompt = f"""
POMYSŁ UŻYTKOWNIKA:
{topic}

Przygotuj jeden draft Shorta. Wymagania:
- hook w pierwszych 2 sekundach i natychmiastowe pytanie/napięcie,
- jedna główna tajemnica, rosnące napięcie, mocny twist w scenie 8,
- naturalny polski język narracji,
- narration 70-160 słów,
- dokładnie 8 scen, id 1..8,
- każda scena: krótki text narracyjny oraz szczegółowy prompt obrazu 9:16,
- fotorealistyczny polski thriller dokumentalny, bez gore, bez nadnaturalnego potwora,
- continuity_refs używaj do stabilnych nazw encji, np. night_guard, station_platform,
- Visual Bible ma zawierać 1 globalny style oraz tylko potrzebne character/location/object/rule,
- style i rule są globalne; assignments mają przypisywać tylko encje scenowe do scen.

Zwróć JSON dokładnie w strukturze:
{{
  "project": {{
    "id": "stable-slug",
    "title": "...",
    "series": "Ciemna Strona Polski",
    "fictional": true,
    "status": "draft",
    "narration": "...",
    "visual_style": "...",
    "scenes": [
      {{"id":1,"text":"...","prompt":"...","motion":"static","continuity_refs":["..."],"render":{{"mode":"generate"}}}}
    ]
  }},
  "visual_bible": {{
    "entities": [
      {{"entity_key":"global_style","kind":"style","name":"Global Style","description":"...","prompt_fragment":"...","metadata":{{}},"active":true}}
    ],
    "assignments": {{"1":["entity_key"],"2":[]}}
  }}
}}
""".strip()
        response = self.provider.chat(
            [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            temperature=0.45,
            max_tokens=5000,
        )
        envelope = _extract_json_object(response.text)
        project_raw = envelope.get("project")
        if not isinstance(project_raw, dict):
            raise WizardV2Error("AI draft JSON is missing project object")
        project = dict(project_raw)
        if requested_title:
            project["title"] = requested_title
        if requested_id:
            project["id"] = requested_id
        elif not str(project.get("id") or "").strip():
            project["id"] = _slug(str(project.get("title") or topic))
        project.setdefault("series", "Ciemna Strona Polski")
        project.setdefault("fictional", True)
        project.setdefault("status", "draft")
        try:
            normalized = normalize_wizard_payload(project)
        except (WizardValidationError, TypeError, ValueError) as exc:
            raise WizardV2Error(str(exc)) from exc

        bible = _validate_visual_bible(envelope)
        project_model = project_from_short(normalized)
        audit = ShotDirector().audit(project_model.scenes)
        return WizardV2Draft(
            draft=normalized,
            visual_bible=bible,
            shot_audit={"score": audit.score, "ok": audit.ok, "warnings": audit.warnings},
            provider={
                "name": response.provider,
                "model": response.model,
                "usage": dict(response.usage),
            },
        )


def create_reviewed_wizard_v2(
    store: StudioStore,
    *,
    shorts_dir: str | Path,
    envelope: dict[str, Any],
) -> dict[str, Any]:
    draft = envelope.get("draft")
    if not isinstance(draft, dict):
        raise WizardV2Error("reviewed Wizard V2 payload must contain draft")
    try:
        normalized = normalize_wizard_payload(draft)
    except (WizardValidationError, TypeError, ValueError) as exc:
        raise WizardV2Error(str(exc)) from exc
    bible_payload = _validate_visual_bible({"visual_bible": envelope.get("visual_bible") or {}})

    result = NewShortWizard(store, shorts_dir=shorts_dir).create(normalized)
    project_id = str(result["project"]["project_id"])
    bible = VisualBible(store)
    for raw in bible_payload["entities"]:
        bible.upsert(
            VisualBibleEntity(
                project_id=project_id,
                entity_key=raw["entity_key"],
                kind=raw["kind"],
                name=raw["name"],
                description=raw["description"],
                prompt_fragment=raw["prompt_fragment"],
                reference_asset_path=None,
                metadata=raw["metadata"],
                active=raw["active"],
            )
        )
    for scene_id in range(1, 9):
        bible.assign(project_id, scene_id, bible_payload["assignments"].get(str(scene_id), []))

    project = project_from_short(normalized, result["source_yaml"])
    audit = ShotDirector().audit(project.scenes)
    return {
        **result,
        "visual_bible": bible_payload,
        "shot_audit": {"score": audit.score, "ok": audit.ok, "warnings": audit.warnings},
    }
