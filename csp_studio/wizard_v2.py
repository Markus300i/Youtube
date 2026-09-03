from __future__ import annotations

import inspect
import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .import_short import project_from_short
from .local_env import get_local_setting
from .new_short_wizard import NewShortWizard, WizardValidationError, normalize_wizard_payload
from .providers.base import ChatProvider
from .shot_director import ShotDirector
from .store import StudioStore
from .visual_bible import VALID_KINDS, VisualBible, VisualBibleEntity

ENTITY_KEY_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}$")
DEFAULT_WIZARD_NIM_TIMEOUT = 180.0
DEFAULT_WIZARD_NIM_RETRIES = 1
GLOBAL_VISUAL_BIBLE_KINDS = {"style", "rule"}


class WizardV2Error(ValueError):
    pass


def _slug(value: str) -> str:
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_value).strip("-").lower()
    return slug[:64] or "new-short"


def normalize_whitespace(value: str) -> str:
    return " ".join(str(value or "").split())


def _boundary_kind(token: str) -> str | None:
    stripped = token.rstrip('"\'”’»)]}')
    if re.search(r"[.!?…]+$", stripped):
        return "sentence"
    if re.search(r"[,;:]+$", stripped) or stripped in {"-", "–", "—"}:
        return "clause"
    return None


def split_narration_into_scenes(narration: str, count: int = 8) -> list[str]:
    """Split narration without changing its normalized words or their order."""

    normalized = normalize_whitespace(narration)
    words = normalized.split()
    if count <= 0:
        raise WizardV2Error("scene split count must be greater than zero")
    if len(words) < count:
        raise WizardV2Error(
            f"narration has too few words for {count} non-empty scenes: {len(words)}"
        )

    scenes: list[str] = []
    start = 0
    for index in range(count - 1):
        remaining_scenes = count - index
        remaining_words = len(words) - start
        ideal_size = max(1, round(remaining_words / remaining_scenes))
        ideal_end = start + ideal_size
        min_end = start + 1
        max_end = len(words) - (remaining_scenes - 1)
        radius = max(2, ideal_size // 2)
        window_start = max(min_end, ideal_end - radius)
        window_end = min(max_end, ideal_end + radius)

        boundaries: dict[str, list[int]] = {"sentence": [], "clause": []}
        for end in range(window_start, window_end + 1):
            kind = _boundary_kind(words[end - 1])
            if kind:
                boundaries[kind].append(end)

        end = ideal_end
        for kind in ("sentence", "clause"):
            if boundaries[kind]:
                end = min(boundaries[kind], key=lambda candidate: (abs(candidate - ideal_end), candidate))
                break
        end = min(max(end, min_end), max_end)
        scenes.append(" ".join(words[start:end]))
        start = end
    scenes.append(" ".join(words[start:]))

    if len(scenes) != count or any(not scene for scene in scenes):
        raise WizardV2Error(f"deterministic narration split did not produce {count} non-empty scenes")
    if normalize_whitespace(" ".join(scenes)) != normalized:
        raise WizardV2Error("deterministic narration split changed narration content")
    return scenes


def _sum_usage(responses: list[Any]) -> dict[str, int | float]:
    totals: dict[str, int | float] = {}
    for response in responses:
        for key, value in dict(response.usage or {}).items():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            totals[key] = totals.get(key, 0) + value
    return totals


def _setting_float(name: str, default: float) -> float:
    raw = get_local_setting(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise WizardV2Error(f"{name} must be a number") from exc
    if value <= 0:
        raise WizardV2Error(f"{name} must be greater than zero")
    return value


def _setting_int(name: str, default: int) -> int:
    raw = get_local_setting(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise WizardV2Error(f"{name} must be an integer") from exc
    if value < 0:
        raise WizardV2Error(f"{name} cannot be negative")
    return value


def _extract_json_object(text: str) -> dict[str, Any]:
    """Return the first complete JSON object and ignore trailing model chatter/data.

    NIM models occasionally append prose or even a second JSON value after an
    otherwise valid response.  CSP Studio only accepts the first complete object;
    it never merges multiple values or guesses missing fields.
    """

    value = str(text or "").strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*", "", value, flags=re.IGNORECASE)

    start = value.find("{")
    if start < 0:
        raise WizardV2Error("AI draft did not contain a JSON object")

    candidate = value[start:]
    decoder = json.JSONDecoder()
    try:
        payload, _end = decoder.raw_decode(candidate)
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


def _validate_continuity_contract(
    normalized_project: dict[str, Any],
    visual_bible: dict[str, Any],
) -> None:
    entities_by_key = {
        str(entity["entity_key"]): entity
        for entity in visual_bible.get("entities") or []
    }
    assignments = visual_bible.get("assignments") or {}

    for scene in normalized_project.get("scenes") or []:
        scene_id = int(scene["id"])
        raw_refs = scene.get("continuity_refs")
        if not isinstance(raw_refs, list):
            raise WizardV2Error(f"scene {scene_id} continuity_refs must be a list")

        refs: list[str] = []
        seen: set[str] = set()
        duplicates: set[str] = set()
        for raw_ref in raw_refs:
            if not isinstance(raw_ref, str) or not raw_ref.strip() or raw_ref != raw_ref.strip():
                raise WizardV2Error(f"scene {scene_id} has invalid continuity_ref: {raw_ref!r}")
            if raw_ref in seen:
                duplicates.add(raw_ref)
            seen.add(raw_ref)
            refs.append(raw_ref)
        if duplicates:
            raise WizardV2Error(
                f"scene {scene_id} has duplicate continuity_refs: {', '.join(sorted(duplicates))}"
            )

        missing = sorted(ref for ref in refs if ref not in entities_by_key)
        if missing:
            raise WizardV2Error(
                f"scene {scene_id} references undefined Visual Bible entities: {', '.join(missing)}"
            )

        global_refs = sorted(
            ref
            for ref in refs
            if str(entities_by_key[ref].get("kind") or "") in GLOBAL_VISUAL_BIBLE_KINDS
        )
        if global_refs:
            raise WizardV2Error(
                f"scene {scene_id} references global Visual Bible entities: {', '.join(global_refs)}"
            )

        assigned = list(assignments.get(str(scene_id), []))
        global_assignments = sorted(
            key
            for key in assigned
            if str(entities_by_key[key].get("kind") or "") in GLOBAL_VISUAL_BIBLE_KINDS
        )
        if global_assignments:
            raise WizardV2Error(
                f"scene {scene_id} assigns global Visual Bible entities: {', '.join(global_assignments)}"
            )

        if set(assigned) != set(refs):
            raise WizardV2Error(
                f"scene {scene_id} continuity_refs and Visual Bible assignments differ "
                f"(continuity_refs: {', '.join(sorted(refs)) or '(none)'}; "
                f"assignments: {', '.join(sorted(assigned)) or '(none)'})"
            )


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
    """Two-stage AI draft layer in front of the deterministic V1 creation gate."""

    def __init__(self, provider: ChatProvider):
        self.provider = provider

    def _chat(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float,
        max_tokens: int,
    ):
        kwargs: dict[str, Any] = {
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        try:
            parameters = inspect.signature(self.provider.chat).parameters
        except (TypeError, ValueError):
            parameters = {}
        if "request_timeout" in parameters:
            kwargs["request_timeout"] = _setting_float("CSP_WIZARD_NIM_TIMEOUT", DEFAULT_WIZARD_NIM_TIMEOUT)
        if "retries" in parameters:
            kwargs["retries"] = _setting_int("CSP_WIZARD_NIM_RETRIES", DEFAULT_WIZARD_NIM_RETRIES)
        return self.provider.chat(messages, **kwargs)

    @staticmethod
    def _validated_story(response) -> dict[str, str]:
        payload = _extract_json_object(response.text)
        title = normalize_whitespace(payload.get("title"))
        narration = normalize_whitespace(payload.get("narration"))
        if not title:
            raise WizardV2Error("story title is required")
        word_count = len(narration.split())
        if word_count < 70 or word_count > 160:
            raise WizardV2Error(f"narration must contain 70-160 words, got {word_count}")
        return {"title": title, "narration": narration}

    def _generate_story(
        self,
        topic: str,
        *,
        requested_title: str,
    ) -> tuple[dict[str, str], list[Any], int]:
        system = (
            "Tworzysz wyłącznie oryginalne, jawnie fikcyjne historie YouTube Shorts dla kanału "
            "Ciemna Strona Polski. Akcja jest osadzona we współczesnych polskich realiach i ma ton "
            "spokojnego thrillera dokumentalnego. Nie przedstawiaj fikcji jako faktu i nie używaj "
            "prawdziwych ofiar ani nierozwiązanych spraw. Zwróć wyłącznie jeden poprawny JSON bez markdownu."
        )
        title_hint = f"\nTYTUŁ UŻYTKOWNIKA: {requested_title}" if requested_title else ""
        prompt = f"""
POMYSŁ UŻYTKOWNIKA:
{topic}{title_hint}

Napisz mały Story Draft w naturalnym języku polskim:
- natychmiastowy hook,
- jedna główna tajemnica,
- wyraźna eskalacja,
- mocny twist na końcu,
- TARGET 90-120 słów narracji,
- absolutnie wymagane 70-160 słów narracji,
- historia musi pozostać jawnie fikcyjna.

Zwróć wyłącznie:
{{"title":"...","narration":"..."}}
""".strip()
        messages = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
        response = self._chat(messages, temperature=0.3, max_tokens=1200)
        responses = [response]
        try:
            story = self._validated_story(response)
            return story, responses, 0
        except WizardV2Error as first_error:
            repair_prompt = (
                "Poprzedni Story Draft nie przeszedł deterministycznej walidacji CSP Studio. "
                f"BŁĄD WALIDACJI: {first_error}. "
                "Napraw wyłącznie title i narration. Narration musi mieć 70-160 słów, najlepiej 90-120, "
                "z zachowaniem hooka, eskalacji i twistu. Zwróć wyłącznie JSON "
                '{"title":"...","narration":"..."}. Nie dodawaj scen ani Visual Bible.\n\n'
                "POPRZEDNIA ODPOWIEDŹ:\n"
                + response.text
            )
            repaired = self._chat(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                    {"role": "assistant", "content": response.text},
                    {"role": "user", "content": repair_prompt},
                ],
                temperature=0.3,
                max_tokens=1200,
            )
            responses.append(repaired)
            try:
                story = self._validated_story(repaired)
                return story, responses, 1
            except WizardV2Error as repaired_error:
                raise WizardV2Error(
                    f"AI story invalid after one repair attempt: {repaired_error}"
                ) from repaired_error

    @staticmethod
    def _validated_visual_plan(
        response,
        *,
        project_id: str,
        title: str,
        narration: str,
        scene_texts: list[str],
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        visual_plan = _extract_json_object(response.text)
        visual_style = normalize_whitespace(visual_plan.get("visual_style"))
        if not visual_style:
            raise WizardV2Error("visual_style is required")
        raw_scenes = visual_plan.get("scenes")
        if not isinstance(raw_scenes, list) or len(raw_scenes) != 8:
            raise WizardV2Error("visual plan requires exactly 8 scenes")

        ids: list[int] = []
        scenes: list[dict[str, Any]] = []
        for raw_scene in raw_scenes:
            if not isinstance(raw_scene, dict):
                raise WizardV2Error("each visual scene must be an object")
            try:
                scene_id = int(raw_scene.get("id", 0))
            except (TypeError, ValueError) as exc:
                raise WizardV2Error("visual scene id must be an integer") from exc
            ids.append(scene_id)
            prompt = str(raw_scene.get("prompt") or "").strip()
            if not prompt:
                raise WizardV2Error(f"scene {scene_id}: prompt is required")
            continuity_refs = raw_scene.get("continuity_refs")
            if not isinstance(continuity_refs, list):
                raise WizardV2Error(f"scene {scene_id} continuity_refs must be a list")
            render = raw_scene.get("render")
            if not isinstance(render, dict) or render.get("mode") != "generate":
                raise WizardV2Error(f"scene {scene_id} render.mode must be generate")
            scenes.append(
                {
                    "id": scene_id,
                    "text": scene_texts[scene_id - 1] if scene_id in range(1, 9) else "",
                    "prompt": prompt,
                    "motion": str(raw_scene.get("motion") or "static").strip() or "static",
                    "continuity_refs": list(continuity_refs),
                    "render": dict(render),
                }
            )
        if ids != list(range(1, 9)):
            raise WizardV2Error("visual scene ids must be exactly 1..8 in order")

        project = {
            "id": project_id,
            "title": title,
            "series": "Ciemna Strona Polski",
            "fictional": True,
            "status": "draft",
            "narration": narration,
            "visual_style": visual_style,
            "scenes": scenes,
        }
        try:
            normalized = normalize_wizard_payload(project)
        except (WizardValidationError, TypeError, ValueError) as exc:
            raise WizardV2Error(str(exc)) from exc
        if normalize_whitespace(normalized["narration"]) != normalize_whitespace(narration):
            raise WizardV2Error("visual plan changed canonical narration")

        bible = _validate_visual_bible(visual_plan)
        _validate_continuity_contract(normalized, bible)
        project_model = project_from_short(normalized)
        audit = ShotDirector().audit(project_model.scenes)
        return (
            normalized,
            bible,
            {"score": audit.score, "ok": audit.ok, "warnings": audit.warnings},
        )

    def _generate_visual_plan(
        self,
        *,
        project_id: str,
        title: str,
        narration: str,
        scene_texts: list[str],
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], list[Any], int]:
        system = (
            "Jesteś Visual Plannerem fikcyjnych YouTube Shorts Ciemna Strona Polski. Otrzymujesz zamknięte, "
            "kanoniczne teksty ośmiu scen. Nie wolno ich zmieniać, parafrazować ani zwracać nowej narracji. "
            "Zwróć wyłącznie jeden poprawny JSON, bez markdownu, analizy i komentarzy. "
            "Tablica scenes MUSI mieć dokładnie 8 elementów o id 1,2,3,4,5,6,7,8. "
            "Każdy continuity_ref MUSI wskazywać istniejące visual_bible.entities.entity_key, a assignments każdej sceny "
            "MUSZĄ być dokładnie równe jej continuity_refs. Style i rule są globalne i nie mogą występować w continuity_refs ani assignments."
        )
        immutable_scenes = [
            {"id": scene_id, "text": scene_texts[scene_id - 1]}
            for scene_id in range(1, 9)
        ]
        prompt = f"""
TYTUŁ:
{title}

CANONICAL NARRATION — IMMUTABLE:
{narration}

CANONICAL SCENE TEXTS — IMMUTABLE:
{json.dumps(immutable_scenes, ensure_ascii=False, indent=2)}

Przygotuj wyłącznie plan wizualny:
- DOKŁADNIE 8 scen o id kolejno 1..8,
- każda scena ma szczegółowy prompt obrazu 9:16, motion, continuity_refs i render.mode=generate,
- nie generuj narration ani scene.text; każde zwrócone pole text zostanie zignorowane,
- fotorealistyczny polski thriller dokumentalny bez gore i nadnaturalnego potwora,
- continuity_refs używaj wyłącznie dla stabilnych encji wizualnych wymagających ciągłości, np. character, location, object, wardrobe, vehicle lub lighting,
- nie używaj abstrakcyjnych wydarzeń ani pojęć jako continuity_refs, np. realization,
- KAŻDY continuity_ref musi mieć odpowiadające visual_bible.entities.entity_key,
- jeśli używasz night_guard, station_platform, monitoring_screen itp., każda taka nazwa MUSI istnieć jako entity_key w Visual Bible,
- Visual Bible ma zawierać 1 globalny style oraz tylko potrzebne encje scenowe i globalne reguły,
- style i rule są globalne; NIE umieszczaj ich w continuity_refs ani assignments,
- assignments każdej sceny MUSZĄ zawierać dokładnie te same entity_key co continuity_refs tej sceny; kolejność nie ma znaczenia.

Zwróć JSON dokładnie w strukturze:
{{
  "visual_style": "...",
  "scenes": [
    {{"id":1,"prompt":"...","motion":"static","continuity_refs":["night_guard","station_platform"],"render":{{"mode":"generate"}}}},
    {{"id":2,"prompt":"...","motion":"static","continuity_refs":["night_guard","station_platform"],"render":{{"mode":"generate"}}}},
    {{"id":3,"prompt":"...","motion":"static","continuity_refs":["night_guard","station_platform"],"render":{{"mode":"generate"}}}},
    {{"id":4,"prompt":"...","motion":"static","continuity_refs":["night_guard","station_platform"],"render":{{"mode":"generate"}}}},
    {{"id":5,"prompt":"...","motion":"static","continuity_refs":["night_guard","station_platform"],"render":{{"mode":"generate"}}}},
    {{"id":6,"prompt":"...","motion":"static","continuity_refs":["night_guard","station_platform"],"render":{{"mode":"generate"}}}},
    {{"id":7,"prompt":"...","motion":"static","continuity_refs":["night_guard","station_platform"],"render":{{"mode":"generate"}}}},
    {{"id":8,"prompt":"...","motion":"static","continuity_refs":["night_guard","station_platform"],"render":{{"mode":"generate"}}}}
  ],
  "visual_bible": {{
    "entities": [
      {{"entity_key":"global_style","kind":"style","name":"Global Style","description":"...","prompt_fragment":"...","metadata":{{}},"active":true}},
      {{"entity_key":"night_guard","kind":"character","name":"Night Guard","description":"...","prompt_fragment":"...","metadata":{{}},"active":true}},
      {{"entity_key":"station_platform","kind":"location","name":"Station Platform","description":"...","prompt_fragment":"...","metadata":{{}},"active":true}}
    ],
    "assignments": {{"1":["night_guard","station_platform"],"2":["night_guard","station_platform"],"3":["night_guard","station_platform"],"4":["night_guard","station_platform"],"5":["night_guard","station_platform"],"6":["night_guard","station_platform"],"7":["night_guard","station_platform"],"8":["night_guard","station_platform"]}}
  }}
}}
""".strip()
        messages = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
        response = self._chat(messages, temperature=0.2, max_tokens=3800)
        responses = [response]
        try:
            normalized, bible, audit = self._validated_visual_plan(
                response,
                project_id=project_id,
                title=title,
                narration=narration,
                scene_texts=scene_texts,
            )
            return normalized, bible, audit, responses, 0
        except WizardV2Error as first_error:
            repair_prompt = (
                "Poprzedni plan wizualny nie przeszedł deterministycznej walidacji CSP Studio. "
                f"BŁĄD WALIDACJI: {first_error}. "
                "Napraw wyłącznie visual_style, osiem visual scenes oraz Visual Bible. Nie zmieniaj title, narration "
                "ani immutable canonical scene texts wklejonych poniżej. Nie zwracaj nowych scene.text. "
                "Scenes muszą mieć dokładnie id 1..8, prompt, continuity_refs i render.mode=generate. "
                "Każdy continuity_ref musi wskazywać istniejącą, nieglobalną encję Visual Bible; "
                "style i rule nie mogą występować w continuity_refs ani assignments; assignments każdej sceny muszą dokładnie odpowiadać jej continuity_refs. "
                "Zdefiniuj brakujące encje Visual Bible albo usuń niepotrzebne continuity_refs zgodnie z sensem historii; kod nie zrobi tego automatycznie. "
                "Zwróć WYŁĄCZNIE cały poprawiony visual-plan JSON, bez wyjaśnień.\n\n"
                "IMMUTABLE CANONICAL SCENE TEXTS:\n"
                + json.dumps(immutable_scenes, ensure_ascii=False, indent=2)
                + "\n\n"
                "POPRZEDNIA ODPOWIEDŹ:\n"
                + response.text
            )
            repaired = self._chat(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                    {"role": "assistant", "content": response.text},
                    {"role": "user", "content": repair_prompt},
                ],
                temperature=0.2,
                max_tokens=3800,
            )
            responses.append(repaired)
            try:
                normalized, bible, audit = self._validated_visual_plan(
                    repaired,
                    project_id=project_id,
                    title=title,
                    narration=narration,
                    scene_texts=scene_texts,
                )
                return normalized, bible, audit, responses, 1
            except WizardV2Error as repaired_error:
                raise WizardV2Error(
                    f"AI visual plan invalid after one repair attempt: {repaired_error}"
                ) from repaired_error

    def draft(self, topic: str, *, project_id: str | None = None, title: str | None = None) -> WizardV2Draft:
        topic = str(topic or "").strip()
        if not topic:
            raise WizardV2Error("topic is required")
        requested_id = str(project_id or "").strip()
        requested_title = str(title or "").strip()

        story, story_responses, story_repairs = self._generate_story(
            topic,
            requested_title=requested_title,
        )
        final_title = requested_title or story["title"]
        final_project_id = requested_id or _slug(final_title or topic)
        narration = story["narration"]
        scene_texts = split_narration_into_scenes(narration)
        normalized, bible, audit, visual_responses, visual_repairs = self._generate_visual_plan(
            project_id=final_project_id,
            title=final_title,
            narration=narration,
            scene_texts=scene_texts,
        )
        final_response = visual_responses[-1]
        story_usage = _sum_usage(story_responses)
        visual_usage = _sum_usage(visual_responses)
        total_repairs = story_repairs + visual_repairs
        return WizardV2Draft(
            draft=normalized,
            visual_bible=bible,
            shot_audit=audit,
            provider={
                "name": final_response.provider,
                "model": final_response.model,
                "usage": _sum_usage(story_responses + visual_responses),
                "story_usage": story_usage,
                "visual_usage": visual_usage,
                "story_repairs": story_repairs,
                "visual_repairs": visual_repairs,
                "repairs": total_repairs,
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
    _validate_continuity_contract(normalized, bible_payload)

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
