from __future__ import annotations

import argparse
import sys
from common import load_yaml

ALLOWED_MODELS = {"z-image-turbo", "flux2-klein"}
ALLOWED_STATUS = {"draft", "approved", "ready"}


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

    ids = [s.get("id") for s in scenes]
    if ids != list(range(1, 9)):
        errors.append("Sceny muszą mieć id od 1 do 8 w kolejności")

    for scene in scenes:
        if not scene.get("prompt"):
            errors.append(f"Scena {scene.get('id')} nie ma promptu")
        if not scene.get("text"):
            errors.append(f"Scena {scene.get('id')} nie ma tekstu narracji")

    narration_words = len(str(data.get("narration", "")).split())
    if narration_words < 70:
        errors.append(f"Narracja wygląda na zbyt krótką ({narration_words} słów)")
    if narration_words > 160:
        errors.append(f"Narracja wygląda na zbyt długą ({narration_words} słów)")

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
    return 0


if __name__ == "__main__":
    sys.exit(main())
