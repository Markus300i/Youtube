from __future__ import annotations

import argparse
import difflib
import re
import sys

from common import load_yaml

ALLOWED_MODELS = {"z-image-turbo", "flux2-klein"}
ALLOWED_STATUS = {"draft", "approved", "ready"}
ALLOWED_MOTIONS = {"static", "push_in", "slow_push", "pan_left", "pan_right"}


def normalize_text(value: str) -> str:
    value = value.lower().replace("…", " ")
    return " ".join(re.findall(r"[0-9a-ząćęłńóśźż]+", value, flags=re.IGNORECASE))


def validate(data: dict) -> list[str]:
    errors: list[str] = []
    for field in ("id", "title", "series", "status", "narration", "scenes"):
        if field not in data:
            errors.append(f"Brak pola: {field}")

    if data.get("fictional") is not True:
        errors.append("fictional musi mieć wartość true")
    if data.get("status") not in ALLOWED_STATUS:
        errors.append(f"Nieprawidłowy status: {data.get('status')}")
    if data.get("image_model") not in ALLOWED_MODELS:
        errors.append(f"Nieobsługiwany image_model: {data.get('image_model')}")

    scenes = data.get("scenes") or []
    if len(scenes) != 8:
        errors.append(f"Short musi mieć dokładnie 8 scen; otrzymano {len(scenes)}")

    ids = [scene.get("id") for scene in scenes]
    if ids != list(range(1, 9)):
        errors.append("Sceny muszą mieć id od 1 do 8 w kolejności")

    scene_texts: list[str] = []
    for scene in scenes:
        scene_id = scene.get("id")
        prompt = str(scene.get("prompt") or "").strip()
        text = str(scene.get("text") or "").strip()
        motion = str(scene.get("motion") or "static").strip()

        if not prompt:
            errors.append(f"Scena {scene_id} nie ma promptu")
        if not text:
            errors.append(f"Scena {scene_id} nie ma tekstu narracji")
        else:
            scene_texts.append(text)
            words = len(text.split())
            if words > 45:
                errors.append(
                    f"Scena {scene_id} ma {words} słów; maksymalnie 45 dla stabilnego segmentu TTS"
                )
        if motion not in ALLOWED_MOTIONS:
            errors.append(
                f"Scena {scene_id}: nieobsługiwany motion '{motion}'. "
                f"Dozwolone: {', '.join(sorted(ALLOWED_MOTIONS))}"
            )

    narration = str(data.get("narration", ""))
    narration_words = len(narration.split())
    if narration_words < 70:
        errors.append(f"Narracja wygląda na zbyt krótką ({narration_words} słów)")
    if narration_words > 160:
        errors.append(f"Narracja wygląda na zbyt długą ({narration_words} słów)")

    scene_script = " ".join(scene_texts)
    if narration and scene_script:
        narration_norm = normalize_text(narration)
        scenes_norm = normalize_text(scene_script)
        ratio = difflib.SequenceMatcher(None, narration_norm, scenes_norm).ratio()
        if ratio < 0.97:
            errors.append(
                f"Teksty 8 scen nie odpowiadają zatwierdzonej narracji (zgodność {ratio:.0%}). "
                "Pipeline TTS czyta scenes[].text, więc oba skrypty muszą być praktycznie identyczne."
            )

    subtitle_cfg = data.get("subtitles") or {}
    max_words = int(subtitle_cfg.get("max_words", 5))
    if not 2 <= max_words <= 5:
        errors.append("subtitles.max_words musi być w zakresie 2–5")

    output = data.get("output") or {}
    if int(output.get("width", 1080)) != 1080 or int(output.get("height", 1920)) != 1920:
        errors.append("CSP v1 wymaga finalnego formatu 1080x1920")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("short_file")
    args = parser.parse_args()

    data = load_yaml(args.short_file)
    errors = validate(data)
    if errors:
        print("VALIDATION FAILED")
        for error in errors:
            print(f"- {error}")
        return 1

    print(f"OK: {data['id']} — {data['title']} ({data['image_model']})")
    print(f"Narracja: {len(str(data['narration']).split())} słów | 8 scen")
    return 0


if __name__ == "__main__":
    sys.exit(main())
