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
    "empty room, empty corridor, no person, table, desk, trolley, cabinet, chair, "
    "extra furniture, extra pipes, extra doors, additional clutter, cinematic rim light, "
    "warm portrait lighting, plastic skin, bad hands, extra fingers, deformed anatomy, "
    "smooth CGI surfaces, fantasy elements, ghosts, glowing eyes, monster, text, labels, "
    "watermark, over-stylized lighting"
)

# V2 deliberately gives the administrator much more room than the original edit mask.
EDIT_RECTS = [[0.48, 0.20, 1.00, 0.98]]
# Keep the identity-bearing door pixels locked after generation.
PRESERVE_RECTS = [[0.27, 0.34, 0.66, 0.86]]
FEATHER = 0.010

VARIANTS = [
    {
        "name": "A",
        "ip": 0.52,
        "control": 0.12,
        "control_end": 0.30,
        "cfg": 6.0,
        "steps": 28,
        "seed": 1700321,
    },
    {
        "name": "B",
        "ip": 0.42,
        "control": 0.06,
        "control_end": 0.25,
        "cfg": 6.3,
        "steps": 30,
        "seed": 1700322,
    },
    {
        "name": "C",
        "ip": 0.32,
        "control": 0.00,
        "control_end": 0.20,
        "cfg": 6.5,
        "steps": 30,
        "seed": 1700323,
    },
]


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
    admin_emphasis = (
        "IMPORTANT: one clearly visible elderly Polish building administrator MUST be present "
        "in the right half of the frame, three-quarter body visible from about knees upward. "
        "He stands naturally close to the wall without covering the central door, looking down "
        "at an old worn technical folder and yellowed building plans held in both hands. "
        "He is about 65, thinning short grey hair, rectangular reading glasses, tired natural face, "
        "slightly stooped posture, faded dark navy work jacket over a charcoal knitted sweater, "
        "dark trousers. Ordinary long-time apartment-block administrator, not businessman, police, "
        "security guard or cinematic hero. The person must be physically integrated into the same "
        "cold fluorescent basement light and cast believable contact shadows."
    )
    return ". ".join(
        part
        for part in (style, continuity, instruction, scene_prompt, admin_emphasis)
        if part
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


def make_contact_sheet(files: list[tuple[str, Path]], target: Path) -> None:
    opened: list[tuple[str, Image.Image]] = []
    try:
        for label, path in files:
            opened.append((label, Image.open(path).convert("RGB")))
        if not opened:
            return
        width = max(img.width for _, img in opened)
        height = max(img.height for _, img in opened)
        label_h = 44
        sheet = Image.new("RGB", (width * len(opened), height + label_h), "black")
        draw = ImageDraw.Draw(sheet)
        for index, (label, image) in enumerate(opened):
            x = index * width
            sheet.paste(image.resize((width, height), Image.Resampling.LANCZOS), (x, label_h))
            draw.text((x + 16, 14), label, fill="white")
        target.parent.mkdir(parents=True, exist_ok=True)
        sheet.save(target, format="JPEG", quality=92)
    finally:
        for _, image in opened:
            image.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("short_file", nargs="?", default="shorts/001-drzwi-0.yaml")
    parser.add_argument("--reference", required=True)
    parser.add_argument("--checkpoint", default="sd_xl_base_1.0.safetensors")
    parser.add_argument(
        "--controlnet",
        default="controlnet-canny-sdxl-1.0-small-fp16.safetensors",
    )
    args = parser.parse_args()

    short = load_yaml(args.short_file)
    scene = scene_by_id(short, 3)
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

    out_dir = short_output_dir(short) / "compare" / "scene-03" / "sdxl-v2"
    work_dir = out_dir / "work"
    out_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    masked_reference = work_dir / "scene-01-inpaint-reference-v2.png"
    width, height = make_inpaint_reference(reference, masked_reference, EDIT_RECTS[0])
    uploaded = upload_input_image(
        base_url,
        masked_reference,
        f"csp_{short['id']}_scene03_sdxl_inpaint_v2.png",
    )

    template = json.loads(WORKFLOW.read_text(encoding="utf-8"))
    results: list[tuple[str, Path]] = []
    positive = build_positive(short, scene)

    print(f"SDXL/IP-ADAPTER V2 scene 3: {width}x{height}")
    print(f"MASK {EDIT_RECTS[0]}  PRESERVE DOOR {PRESERVE_RECTS[0]}")

    for variant in VARIANTS:
        name = str(variant["name"])
        workflow = copy.deepcopy(template)
        workflow["1"]["inputs"]["image"] = uploaded
        workflow["2"]["inputs"]["ckpt_name"] = args.checkpoint
        workflow["4"]["inputs"]["weight"] = float(variant["ip"])
        workflow["5"]["inputs"]["text"] = positive
        workflow["6"]["inputs"]["text"] = DEFAULT_NEGATIVE
        workflow["8"]["inputs"]["control_net_name"] = args.controlnet
        workflow["9"]["inputs"]["strength"] = float(variant["control"])
        workflow["9"]["inputs"]["end_percent"] = float(variant["control_end"])
        workflow["11"]["inputs"]["seed"] = int(variant["seed"])
        workflow["11"]["inputs"]["steps"] = int(variant["steps"])
        workflow["11"]["inputs"]["cfg"] = float(variant["cfg"])
        workflow["13"]["inputs"]["filename_prefix"] = (
            f"csp_{short['id']}_scene03_sdxl_v2_{name.lower()}"
        )

        raw_target = work_dir / f"variant-{name}-raw.png"
        final_target = out_dir / f"variant-{name}.png"
        print(
            f"VARIANT {name}: IP={variant['ip']} Control={variant['control']} "
            f"end={variant['control_end']} CFG={variant['cfg']} "
            f"steps={variant['steps']} seed={variant['seed']}"
        )

        render_workflow(
            base_url=base_url,
            comfy=comfy,
            workflow=workflow,
            save_node="13",
            target=raw_target,
        )
        composite_edit(
            reference,
            raw_target,
            final_target,
            EDIT_RECTS,
            PRESERVE_RECTS,
            FEATHER,
            width,
            height,
        )
        results.append((f"{name}  IP {variant['ip']}  CN {variant['control']}", final_target))
        print(f"SAVED {final_target}")

    contact_sheet = out_dir / "comparison-ABC.jpg"
    make_contact_sheet(results, contact_sheet)
    print()
    print("A/B/C READY")
    for _, path in results:
        print(path)
    print(f"COMPARE {contact_sheet}")
    free_comfy_memory(base_url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
