from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
from typing import Any

from PIL import Image, ImageEnhance, ImageFilter, ImageDraw

from common import ROOT, load_yaml, short_output_dir
from generate_images import free_comfy_memory, render_workflow, wait_for_comfy

WORKFLOW = ROOT / "workflows/comfyui/sdxl-foreground-person-api.json"

POSITIVE = (
    "exactly one realistic Polish apartment-block administrator, man about 65 years old, "
    "short thinning grey hair, rectangular reading glasses, slightly stooped posture, "
    "weathered ordinary tired face, dark navy worn work jacket over a charcoal knitted sweater, "
    "dark trousers, ordinary black shoes, holding several old yellowed technical building plans "
    "in both hands and looking down at the papers, full body from head to shoes fully visible, "
    "standing upright, natural human proportions, realistic hands, documentary photography, "
    "cold neutral fluorescent light, plain seamless light gray studio background, centered person, "
    "no environmental objects, no furniture, no room, no wall texture"
)

NEGATIVE = (
    "cropped body, close-up portrait, giant person, oversized head, yellow jacket, high visibility vest, "
    "helmet, businessman, police officer, security guard, extra people, duplicate person, extra arms, "
    "extra fingers, malformed hands, deformed anatomy, cartoon, illustration, CGI, dramatic rim light, "
    "basement, corridor, door, pipes, furniture, clutter, text, watermark"
)

VARIANTS = [
    {"name": "G", "seed": 1700501, "height": 0.56, "center_x": 0.78, "floor_y": 0.94},
    {"name": "H", "seed": 1700502, "height": 0.50, "center_x": 0.77, "floor_y": 0.945},
    {"name": "I", "seed": 1700503, "height": 0.46, "center_x": 0.76, "floor_y": 0.95},
]


def trim_alpha(image: Image.Image) -> Image.Image:
    rgba = image.convert("RGBA")
    alpha = rgba.getchannel("A")
    bbox = alpha.point(lambda p: 255 if p > 8 else 0).getbbox()
    if not bbox:
        raise RuntimeError("Background remover returned an empty foreground mask")
    return rgba.crop(bbox)


def remove_background(source: Path, target: Path) -> Image.Image:
    try:
        from rembg import new_session, remove
    except ImportError as exc:
        raise RuntimeError(
            "Missing rembg. Run the PowerShell wrapper; it installs rembg[cpu] automatically."
        ) from exc

    session = new_session("u2net_human_seg")
    with Image.open(source) as image:
        cutout = remove(image.convert("RGB"), session=session)
    cutout = trim_alpha(cutout)
    target.parent.mkdir(parents=True, exist_ok=True)
    cutout.save(target, "PNG", optimize=True)
    return cutout


def grade_foreground(image: Image.Image) -> Image.Image:
    alpha = image.getchannel("A")
    rgb = image.convert("RGB")
    rgb = ImageEnhance.Color(rgb).enhance(0.82)
    rgb = ImageEnhance.Brightness(rgb).enhance(0.88)
    rgb = ImageEnhance.Contrast(rgb).enhance(0.96)
    rgba = rgb.convert("RGBA")
    rgba.putalpha(alpha)
    return rgba


def composite_person(
    master: Path,
    cutout: Image.Image,
    target: Path,
    target_height_fraction: float,
    center_x_fraction: float,
    floor_y_fraction: float,
) -> None:
    with Image.open(master) as source:
        base = source.convert("RGBA")

    person = grade_foreground(cutout)
    target_h = max(1, int(round(base.height * target_height_fraction)))
    scale = target_h / person.height
    target_w = max(1, int(round(person.width * scale)))
    person = person.resize((target_w, target_h), Image.Resampling.LANCZOS)

    center_x = int(round(base.width * center_x_fraction))
    floor_y = int(round(base.height * floor_y_fraction))
    x = center_x - target_w // 2
    y = floor_y - target_h
    x = max(-target_w // 4, min(base.width - (target_w * 3 // 4), x))
    y = max(0, min(base.height - target_h, y))

    alpha = person.getchannel("A")
    shadow_alpha = alpha.filter(ImageFilter.GaussianBlur(radius=max(6, target_h // 80)))
    shadow_alpha = shadow_alpha.point(lambda p: int(p * 0.22))
    shadow = Image.new("RGBA", person.size, (0, 0, 0, 0))
    shadow.putalpha(shadow_alpha)
    base.alpha_composite(shadow, (x + max(4, target_w // 40), y + max(5, target_h // 60)))
    base.alpha_composite(person, (x, y))

    target.parent.mkdir(parents=True, exist_ok=True)
    base.convert("RGB").save(target, "PNG", optimize=True)


def make_comparison(paths: list[Path], target: Path) -> None:
    images = [Image.open(p).convert("RGB") for p in paths]
    try:
        w, h = images[0].size
        header = 30
        canvas = Image.new("RGB", (w * len(images), h + header), "black")
        draw = ImageDraw.Draw(canvas)
        for index, (variant, image) in enumerate(zip(VARIANTS, images)):
            canvas.paste(image, (index * w, header))
            text = (
                f"{variant['name']} seed {variant['seed']} "
                f"height {variant['height']:.2f} x {variant['center_x']:.2f}"
            )
            draw.text((index * w + 8, 8), text, fill="white")
        target.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(target, "JPEG", quality=92)
    finally:
        for image in images:
            image.close()


def validate_nodes(base_url: str) -> None:
    import requests

    required = {
        "CheckpointLoaderSimple",
        "CLIPTextEncode",
        "EmptyLatentImage",
        "KSampler",
        "VAEDecode",
        "SaveImage",
    }
    response = requests.get(f"{base_url}/object_info", timeout=60)
    response.raise_for_status()
    missing = sorted(required - set(response.json().keys()))
    if missing:
        raise RuntimeError("ComfyUI missing nodes: " + ", ".join(missing))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("short_file", nargs="?", default="shorts/001-drzwi-0.yaml")
    parser.add_argument("--reference", required=True)
    parser.add_argument("--checkpoint", default="sd_xl_base_1.0.safetensors")
    parser.add_argument("--steps", type=int, default=32)
    parser.add_argument("--cfg", type=float, default=6.5)
    args = parser.parse_args()

    reference = Path(args.reference).expanduser().resolve()
    if not reference.exists():
        raise FileNotFoundError(f"Missing master: {reference}")
    if not WORKFLOW.exists():
        raise FileNotFoundError(f"Missing workflow: {WORKFLOW}")

    short = load_yaml(args.short_file)
    config = load_yaml("config/models.yaml")
    comfy = config["comfyui"]
    base_url = os.getenv("CSP_COMFY_URL", comfy["base_url"]).rstrip("/")
    wait_for_comfy(base_url)
    validate_nodes(base_url)

    out_dir = short_output_dir(short) / "compare" / "scene-03" / "sdxl-foreground-ghi"
    work_dir = out_dir / "work"
    out_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    template = json.loads(WORKFLOW.read_text(encoding="utf-8"))
    finals: list[Path] = []

    for variant in VARIANTS:
        name = str(variant["name"])
        raw = work_dir / f"foreground-{name}-raw.png"
        cutout_path = work_dir / f"foreground-{name}-cutout.png"
        final = out_dir / f"variant-{name}.png"

        workflow = copy.deepcopy(template)
        workflow["1"]["inputs"]["ckpt_name"] = args.checkpoint
        workflow["2"]["inputs"]["text"] = POSITIVE
        workflow["3"]["inputs"]["text"] = NEGATIVE
        workflow["5"]["inputs"]["seed"] = int(variant["seed"])
        workflow["5"]["inputs"]["steps"] = int(args.steps)
        workflow["5"]["inputs"]["cfg"] = float(args.cfg)
        workflow["7"]["inputs"]["filename_prefix"] = f"csp_scene03_foreground_{name}"

        print(f"FOREGROUND {name}: seed={variant['seed']} steps={args.steps} cfg={args.cfg}")
        render_workflow(
            base_url=base_url,
            comfy=comfy,
            workflow=workflow,
            save_node="7",
            target=raw,
        )
        cutout = remove_background(raw, cutout_path)
        composite_person(
            reference,
            cutout,
            final,
            float(variant["height"]),
            float(variant["center_x"]),
            float(variant["floor_y"]),
        )
        finals.append(final)
        print(f"FINAL {name}: {final}")

    comparison = out_dir / "comparison-GHI.jpg"
    make_comparison(finals, comparison)
    print(f"COMPARE {comparison}")
    free_comfy_memory(base_url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
