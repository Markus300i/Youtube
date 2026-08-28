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

WORKFLOW = ROOT / "workflows/comfyui/sdxl-inpaint-scene3-api.json"

POSITIVE = (
    "One realistic 65-year-old Polish apartment building administrator standing in the right third of the frame, "
    "visible from head to knees. Short thinning grey hair, rectangular reading glasses, slightly stooped posture, "
    "ordinary tired weathered face. He wears a worn dark navy work jacket over a charcoal knitted sweater, dark trousers, "
    "ordinary black shoes. He holds several old yellowed technical building plans in both hands and looks down at them. "
    "Exactly one man. The man must be clearly visible and occupy a substantial part of the right side of the image. "
    "Natural human proportions, realistic hands, documentary photography, cold fluorescent basement lighting, "
    "believable integration into an old Polish apartment-block basement, subtle psychological thriller atmosphere."
)

NEGATIVE = (
    "empty room, no person, no people, table, desk, chair, cabinet, extra furniture, extra door, extra doorway, "
    "extra pipes, duplicate pipe, duplicate door, police uniform, security guard, businessman, suit, tie, cinematic hero, "
    "plastic skin, glamour portrait, dramatic rim light, bad hands, extra fingers, missing fingers, deformed anatomy, "
    "duplicate person, two people, cropped head, floating body, ghost, monster, supernatural glow, text, watermark, CGI"
)

VARIANTS = [
    {
        "name": "D",
        "rect": [0.58, 0.15, 0.99, 0.98],
        "seed": 1700410,
        "steps": 32,
        "cfg": 6.6,
    },
    {
        "name": "E",
        "rect": [0.50, 0.12, 0.99, 0.99],
        "seed": 1700411,
        "steps": 32,
        "cfg": 6.8,
    },
    {
        "name": "F",
        "rect": [0.42, 0.10, 0.99, 0.99],
        "seed": 1700412,
        "steps": 34,
        "cfg": 7.0,
    },
]


def normalized_box(rect: Any, width: int, height: int) -> tuple[int, int, int, int]:
    left, top, right, bottom = [float(v) for v in rect]
    return (
        int(round(max(0.0, min(1.0, left)) * width)),
        int(round(max(0.0, min(1.0, top)) * height)),
        int(round(max(0.0, min(1.0, right)) * width)),
        int(round(max(0.0, min(1.0, bottom)) * height)),
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
        "CLIPTextEncode",
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
        raise RuntimeError("ComfyUI nie ma wymaganych core node'ów: " + ", ".join(missing))


def make_comparison(paths: list[Path], labels: list[str], target: Path) -> None:
    images: list[Image.Image] = []
    for path in paths:
        with Image.open(path) as source:
            images.append(source.convert("RGB"))

    tile_w = min(image.width for image in images)
    tile_h = min(image.height for image in images)
    header = 34
    sheet = Image.new("RGB", (tile_w * len(images), tile_h + header), (0, 0, 0))
    draw = ImageDraw.Draw(sheet)

    for index, (image, label) in enumerate(zip(images, labels)):
        image = image.resize((tile_w, tile_h), Image.Resampling.LANCZOS)
        x = index * tile_w
        sheet.paste(image, (x, header))
        draw.text((x + 10, 10), label, fill=(255, 255, 255))

    target.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(target, format="JPEG", quality=94)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("short_file", nargs="?", default="shorts/001-drzwi-0.yaml")
    parser.add_argument("--reference", required=True)
    parser.add_argument("--checkpoint", default="sd_xl_base_1.0.safetensors")
    args = parser.parse_args()

    reference = Path(args.reference).expanduser().resolve()
    if not reference.exists():
        raise FileNotFoundError(f"Brak mastera: {reference}")
    if not WORKFLOW.exists():
        raise FileNotFoundError(f"Brak workflow: {WORKFLOW}")

    short = load_yaml(args.short_file)
    config = load_yaml("config/models.yaml")
    comfy = config["comfyui"]
    base_url = os.getenv("CSP_COMFY_URL", comfy["base_url"]).rstrip("/")
    wait_for_comfy(base_url)
    validate_nodes(base_url)

    out_dir = short_output_dir(short) / "compare" / "scene-03" / "sdxl-inpaint-def"
    work_dir = out_dir / "work"
    out_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    template = json.loads(WORKFLOW.read_text(encoding="utf-8"))
    finals: list[Path] = []
    labels: list[str] = []

    for variant in VARIANTS:
        name = str(variant["name"])
        rect = variant["rect"]
        seed = int(variant["seed"])
        steps = int(variant["steps"])
        cfg = float(variant["cfg"])

        masked = work_dir / f"variant-{name}-masked.png"
        raw = work_dir / f"variant-{name}-raw.png"
        final = out_dir / f"variant-{name}.png"

        width, height = make_inpaint_reference(reference, masked, rect)
        uploaded = upload_input_image(
            base_url,
            masked,
            f"csp_{short['id']}_scene03_sdxl_inpaint_{name}.png",
        )

        workflow = copy.deepcopy(template)
        workflow["1"]["inputs"]["image"] = uploaded
        workflow["2"]["inputs"]["ckpt_name"] = args.checkpoint
        workflow["3"]["inputs"]["text"] = POSITIVE
        workflow["4"]["inputs"]["text"] = NEGATIVE
        workflow["6"]["inputs"]["seed"] = seed
        workflow["6"]["inputs"]["steps"] = steps
        workflow["6"]["inputs"]["cfg"] = cfg
        workflow["8"]["inputs"]["filename_prefix"] = f"csp_{short['id']}_scene03_sdxl_inpaint_{name}"

        print(
            f"SDXL INPAINT {name}: {width}x{height}, mask={rect}, "
            f"seed={seed}, steps={steps}, cfg={cfg}"
        )
        render_workflow(
            base_url=base_url,
            comfy=comfy,
            workflow=workflow,
            save_node="8",
            target=raw,
        )

        # Hard continuity rule: outside the edit mask we restore the master.
        composite_edit(
            reference,
            raw,
            final,
            [rect],
            None,
            0.008,
            width,
            height,
        )
        print(f"FINAL {final}")
        finals.append(final)
        labels.append(f"{name}  seed {seed}  mask {rect[0]:.2f}-{rect[2]:.2f}")
        free_comfy_memory(base_url)

    comparison = out_dir / "comparison-DEF.jpg"
    make_comparison(finals, labels, comparison)
    print()
    print("D/E/F READY")
    print(comparison)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
