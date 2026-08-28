from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
from typing import Any

import requests
from PIL import Image, ImageDraw

from common import ROOT, load_yaml, short_output_dir
from generate_images import (
    composite_edit,
    free_comfy_memory,
    render_workflow,
    upload_input_image,
    wait_for_comfy,
)


WORKFLOW = ROOT / "workflows/comfyui/sdxl-ipadapter-controlnet-inpaint-api.json"
DEFAULT_NEGATIVE = (
    "table, desk, trolley, cabinet, chair, extra furniture, extra pipes, extra doors, "
    "additional clutter, cinematic rim light, warm portrait lighting, plastic skin, "
    "bad hands, extra fingers, deformed anatomy, smooth CGI surfaces, fantasy elements, "
    "ghosts, glowing eyes, monster, text, labels, watermark, over-stylized lighting"
)


def scene_by_id(short: dict[str, Any], scene_id: int) -> dict[str, Any]:
    for scene in short.get("scenes") or []:
        if int(scene.get("id", 0)) == scene_id:
            return scene
    raise ValueError(f"Brak sceny {scene_id}")


def continuity_prompt(short: dict[str, Any], scene: dict[str, Any]) -> str:
    continuity = short.get("continuity") or {}
    parts: list[str] = []
    global_text = str(continuity.get("global") or "").strip()
    if global_text:
        parts.append(global_text)
    anchors = continuity.get("anchors") or {}
    refs = scene.get("continuity_refs") or []
    if isinstance(refs, str):
        refs = [refs]
    if isinstance(anchors, dict):
        for ref in refs:
            value = anchors.get(str(ref))
            if value:
                parts.append(str(value).strip())
    return ". ".join(parts)


def build_positive(short: dict[str, Any], scene: dict[str, Any]) -> str:
    render = scene.get("render") or {}
    instruction = str(render.get("instruction") or "").strip()
    scene_prompt = str(scene.get("prompt") or "").strip()
    style = str(short.get("visual_style") or "").strip()
    continuity = continuity_prompt(short, scene)
    return ". ".join(
        part for part in (style, continuity, instruction, scene_prompt) if part
    )


def normalized_box(rect: Any, width: int, height: int) -> tuple[int, int, int, int]:
    if not isinstance(rect, (list, tuple)) or len(rect) != 4:
        raise ValueError("Mask rect musi mieć cztery wartości 0..1")
    left, top, right, bottom = [float(v) for v in rect]
    left = max(0.0, min(1.0, left))
    top = max(0.0, min(1.0, top))
    right = max(left, min(1.0, right))
    bottom = max(top, min(1.0, bottom))
    return (
        int(round(left * width)),
        int(round(top * height)),
        int(round(right * width)),
        int(round(bottom * height)),
    )


def make_inpaint_reference(reference: Path, target: Path, rect: Any) -> tuple[int, int]:
    with Image.open(reference) as source:
        image = source.convert("RGBA")
    alpha = Image.new("L", image.size, 255)
    draw = ImageDraw.Draw(alpha)
    draw.rectangle(normalized_box(rect, image.width, image.height), fill=0)
    image.putalpha(alpha)
    target.parent.mkdir(parents=True, exist_ok=True)
    image.save(target, format="PNG", optimize=True)
    return image.width, image.height


def validate_nodes(base_url: str) -> None:
    required = {
        "LoadImage",
        "CheckpointLoaderSimple",
        "IPAdapterUnifiedLoader",
        "IPAdapter",
        "CLIPTextEncode",
        "Canny",
        "ControlNetLoader",
        "ControlNetApplyAdvanced",
        "VAEEncodeForInpaint",
        "KSampler",
        "VAEDecode",
        "SaveImage",
    }
    response = requests.get(f"{base_url}/object_info", timeout=60)
    response.raise_for_status()
    available = set(response.json().keys())
    missing = sorted(required - available)
    if missing:
        raise RuntimeError(
            "ComfyUI nie ma wymaganych node'ów: " + ", ".join(missing)
            + ". Zrestartuj ComfyUI po instalacji IPAdapter/ControlNet Aux."
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("short_file", nargs="?", default="shorts/001-drzwi-0.yaml")
    parser.add_argument("--reference", required=True)
    parser.add_argument("--checkpoint", default="sd_xl_base_1.0.safetensors")
    parser.add_argument(
        "--controlnet",
        default="controlnet-canny-sdxl-1.0-small-fp16.safetensors",
    )
    parser.add_argument("--ip-weight", type=float, default=0.72)
    parser.add_argument("--control-strength", type=float, default=0.28)
    parser.add_argument("--control-end", type=float, default=0.55)
    parser.add_argument("--steps", type=int, default=24)
    parser.add_argument("--cfg", type=float, default=5.5)
    parser.add_argument("--seed", type=int, default=1700320)
    args = parser.parse_args()

    short = load_yaml(args.short_file)
    scene = scene_by_id(short, 3)
    render_cfg = scene.get("render") or {}
    edit_rects = render_cfg.get("edit_rects") or [[0.64, 0.32, 0.99, 0.96]]
    if not edit_rects:
        raise RuntimeError("Scena 3 nie ma edit_rects")

    reference = Path(args.reference).expanduser().resolve()
    if not reference.exists():
        raise FileNotFoundError(f"Brak mastera: {reference}")
    if not WORKFLOW.exists():
        raise FileNotFoundError(f"Brak workflow: {WORKFLOW}")

    config = load_yaml("config/models.yaml")
    comfy = config["comfyui"]
    base_url = os.getenv("CSP_COMFY_URL", comfy["base_url"]).rstrip("/")
    wait_for_comfy(base_url)
    validate_nodes(base_url)

    out_dir = short_output_dir(short) / "compare" / "scene-03"
    work_dir = out_dir / "sdxl-ipadapter-work"
    out_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    masked_reference = work_dir / "scene-01-inpaint-reference.png"
    raw_target = work_dir / "sdxl-ipadapter-controlnet-raw.png"
    final_target = out_dir / "sdxl-ipadapter-controlnet.png"

    width, height = make_inpaint_reference(reference, masked_reference, edit_rects[0])
    uploaded = upload_input_image(
        base_url,
        masked_reference,
        f"csp_{short['id']}_scene03_sdxl_inpaint.png",
    )

    workflow = json.loads(WORKFLOW.read_text(encoding="utf-8"))
    workflow["1"]["inputs"]["image"] = uploaded
    workflow["2"]["inputs"]["ckpt_name"] = args.checkpoint
    workflow["4"]["inputs"]["weight"] = float(args.ip_weight)
    workflow["5"]["inputs"]["text"] = build_positive(short, scene)
    workflow["6"]["inputs"]["text"] = DEFAULT_NEGATIVE
    workflow["8"]["inputs"]["control_net_name"] = args.controlnet
    workflow["9"]["inputs"]["strength"] = float(args.control_strength)
    workflow["9"]["inputs"]["end_percent"] = float(args.control_end)
    workflow["11"]["inputs"]["seed"] = int(args.seed)
    workflow["11"]["inputs"]["steps"] = int(args.steps)
    workflow["11"]["inputs"]["cfg"] = float(args.cfg)
    workflow["13"]["inputs"]["filename_prefix"] = (
        f"csp_{short['id']}_scene03_sdxl_ipadapter_controlnet"
    )

    print(
        "SDXL TEST scene 3: "
        f"{width}x{height}, steps={args.steps}, cfg={args.cfg}, "
        f"IP={args.ip_weight}, ControlNet={args.control_strength}"
    )
    render_workflow(
        base_url=base_url,
        comfy=comfy,
        workflow=copy.deepcopy(workflow),
        save_node="13",
        target=raw_target,
    )

    composite_edit(
        reference,
        raw_target,
        final_target,
        edit_rects,
        render_cfg.get("preserve_rects"),
        float(render_cfg.get("feather", 0.014)),
        width,
        height,
    )

    print(f"RAW   {raw_target}")
    print(f"FINAL {final_target}")
    free_comfy_memory(base_url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
