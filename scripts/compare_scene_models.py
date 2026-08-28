from __future__ import annotations

import argparse
import copy
import os
from pathlib import Path
from typing import Any

from common import load_yaml, short_output_dir
from generate_images import (
    apply_binding,
    continuity_prompt,
    deterministic_seed,
    free_comfy_memory,
    load_workflow,
    render_workflow,
    wait_for_comfy,
)


def scene_by_id(short: dict[str, Any], scene_id: int) -> dict[str, Any]:
    for scene in short.get("scenes") or []:
        if int(scene.get("id", 0)) == scene_id:
            return scene
    raise ValueError(f"Brak sceny {scene_id}")


def build_prompt(short: dict[str, Any], scene: dict[str, Any]) -> str:
    style = str(short.get("visual_style", "")).strip()
    continuity = continuity_prompt(short, scene)
    scene_prompt = str(scene.get("prompt") or "").strip()
    return ". ".join(part for part in (style, continuity, scene_prompt) if part)


def bind_t2i(
    workflow: dict[str, Any],
    model_cfg: dict[str, Any],
    prompt: str,
    seed: int,
    prefix: str,
) -> str:
    bindings = model_cfg.get("bindings") or {}
    models = model_cfg.get("models") or {}
    width = int(model_cfg.get("width", 768))
    height = int(model_cfg.get("height", 1344))
    steps = int(model_cfg.get("steps", 8))

    required = {
        "prompt",
        "seed",
        "steps",
        "width",
        "height",
        "unet",
        "clip",
        "vae",
        "save_prefix",
    }
    missing = sorted(required - set(bindings))
    if missing:
        raise RuntimeError("Brak bindings: " + ", ".join(missing))

    apply_binding(workflow, bindings.get("prompt"), prompt)
    apply_binding(workflow, bindings.get("seed"), seed)
    apply_binding(workflow, bindings.get("steps"), steps)
    apply_binding(workflow, bindings.get("width"), width)
    apply_binding(workflow, bindings.get("height"), height)
    apply_binding(workflow, bindings.get("scheduler_width"), width)
    apply_binding(workflow, bindings.get("scheduler_height"), height)
    apply_binding(workflow, bindings.get("cfg"), float(model_cfg.get("cfg", 5.0)))
    apply_binding(workflow, bindings.get("unet"), models.get("unet"))
    apply_binding(workflow, bindings.get("clip"), models.get("clip"))
    apply_binding(workflow, bindings.get("vae"), models.get("vae"))
    apply_binding(workflow, bindings.get("save_prefix"), prefix)
    return str(bindings["save_prefix"]["node"])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("short_file")
    parser.add_argument("scene", type=int, nargs="?", default=1)
    args = parser.parse_args()

    short = load_yaml(args.short_file)
    config = load_yaml("config/models.yaml")
    scene = scene_by_id(short, args.scene)
    prompt = build_prompt(short, scene)
    seed = deterministic_seed(short) + int(scene["id"])

    comfy = config["comfyui"]
    base_url = os.getenv("CSP_COMFY_URL", comfy["base_url"]).rstrip("/")
    wait_for_comfy(base_url)

    out_dir = short_output_dir(short) / "compare" / f"scene-{args.scene:02d}"
    out_dir.mkdir(parents=True, exist_ok=True)

    for model_name in ("z-image-turbo", "flux2-klein"):
        model_cfg = config["image_models"][model_name]
        template, _ = load_workflow(model_cfg)
        workflow = copy.deepcopy(template)
        save_node = bind_t2i(
            workflow,
            model_cfg,
            prompt,
            seed,
            f"csp_compare_scene_{args.scene:02d}_{model_name}",
        )
        target = out_dir / f"{model_name}.png"
        print(
            f"COMPARE scene {args.scene}: {model_name} -> {target.name} "
            f"({model_cfg.get('width')}x{model_cfg.get('height')}, "
            f"steps={model_cfg.get('steps')}, seed={seed})"
        )
        render_workflow(
            base_url=base_url,
            comfy=comfy,
            workflow=workflow,
            save_node=save_node,
            target=target,
        )
        print(f"SAVED {target}")
        free_comfy_memory(base_url)

    print()
    print("A/B READY")
    print(out_dir / "z-image-turbo.png")
    print(out_dir / "flux2-klein.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
