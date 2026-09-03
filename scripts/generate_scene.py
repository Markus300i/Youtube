from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import requests
import yaml

from common import ROOT, load_yaml


def _resilient_wait_history(
    base_url: str,
    prompt_id: str,
    timeout: int,
    poll: int,
) -> dict[str, Any]:
    """Poll ComfyUI without failing on transient API stalls while GPU is busy."""

    deadline = time.time() + timeout
    last_error: Exception | None = None
    timeout_count = 0

    while time.time() < deadline:
        remaining = max(1.0, deadline - time.time())
        request_timeout = min(30.0, remaining)
        try:
            response = requests.get(
                f"{base_url}/history/{prompt_id}",
                timeout=request_timeout,
            )
            response.raise_for_status()
            payload = response.json()
            if prompt_id in payload:
                history = payload[prompt_id]
                status = history.get("status", {})
                if status.get("status_str") == "error":
                    raise RuntimeError(
                        "Błąd wykonania ComfyUI: "
                        + json.dumps(status, ensure_ascii=False)
                    )
                if timeout_count:
                    print(
                        f"COMFY: history API recovered after {timeout_count} transient timeout(s)",
                        flush=True,
                    )
                return history
            last_error = None
        except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectionError) as exc:
            last_error = exc
            timeout_count += 1
            print(
                "WARN: ComfyUI /history chwilowo nie odpowiada "
                f"({type(exc).__name__}); generacja nadal trwa, ponawiam polling "
                f"[{timeout_count}]",
                flush=True,
            )

        sleep_for = max(1, int(poll))
        if time.time() + sleep_for < deadline:
            time.sleep(sleep_for)

    suffix = f"; last API error: {last_error}" if last_error else ""
    raise TimeoutError(f"Timeout ComfyUI dla prompt_id={prompt_id}{suffix}")


def _apply_visual_bible(data: dict[str, Any], scene_id: int) -> dict[str, Any]:
    """Compile Visual Bible V2 into the execution-only scene payload.

    SQLite scene.prompt remains canonical and unchanged. If Studio DB is not
    available (legacy CLI / CI), generation behaves exactly as before.
    """

    db_value = str(os.getenv("CSP_STUDIO_DB") or "").strip()
    project_id = str(data.get("id") or "").strip()
    if not db_value or not project_id:
        return data
    db_path = Path(db_value).expanduser().resolve()
    if not db_path.is_file():
        return data

    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from csp_studio.store import StudioStore
    from csp_studio.visual_bible import VisualBible

    with StudioStore(db_path) as store:
        canonical_scene = store.get_scene(project_id, scene_id)
        if canonical_scene is None:
            return data
        bible = VisualBible(store)
        context = bible.prompt_context(project_id, scene_id)
        entities = bible.assigned(project_id, scene_id)
        global_entities = bible.list(project_id, kind="style") + bible.list(project_id, kind="rule")
        ordered = []
        seen: set[str] = set()
        for entity in global_entities + entities:
            if entity.entity_key in seen:
                continue
            seen.add(entity.entity_key)
            ordered.append(entity)

        for raw in data.get("scenes") or []:
            if not isinstance(raw, dict) or int(raw.get("id", 0)) != scene_id:
                continue
            base_prompt = str(raw.get("prompt") or canonical_scene.prompt).strip()
            if context:
                raw["prompt"] = f"{context}. {base_prompt}"
            raw["visual_bible_context"] = context
            raw["visual_bible_entities"] = [entity.entity_key for entity in ordered]
            raw["visual_bible_reference_assets"] = [
                entity.reference_asset_path
                for entity in ordered
                if entity.reference_asset_path
            ]
            if context:
                print(
                    "VISUAL BIBLE: scene "
                    f"{scene_id:02d} compiled with {len(ordered)} entity/entities",
                    flush=True,
                )
            break
    return data


def _run_generator(temp_path: Path) -> int:
    """Run generate_images in-process so scene jobs can harden ComfyUI polling."""

    import generate_images

    generate_images.wait_history = _resilient_wait_history
    previous_argv = sys.argv[:]
    sys.argv = [
        str(ROOT / "scripts" / "generate_images.py"),
        str(temp_path),
        "--force",
    ]
    try:
        generate_images.main()
        return 0
    finally:
        sys.argv = previous_argv


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("short_file")
    parser.add_argument("scene", type=int, choices=range(1, 9))
    args = parser.parse_args()

    data = load_yaml(args.short_file)
    data = _apply_visual_bible(data, args.scene)
    selected = [scene for scene in (data.get("scenes") or []) if int(scene.get("id", 0)) == args.scene]
    if not selected:
        raise SystemExit(f"Scene {args.scene} not found in {args.short_file}")

    data["scenes"] = selected
    with tempfile.TemporaryDirectory(prefix="csp-scene-") as temp_dir:
        temp_path = Path(temp_dir) / "short.yaml"
        temp_path.write_text(
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        return _run_generator(temp_path)


if __name__ == "__main__":
    raise SystemExit(main())
