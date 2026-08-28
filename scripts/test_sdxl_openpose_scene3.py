from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
from typing import Any

import requests
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from common import ROOT, load_yaml, short_output_dir
from generate_images import (
    free_comfy_memory,
    render_workflow,
    upload_input_image,
    wait_for_comfy,
)


WORKFLOW = ROOT / "workflows/comfyui/sdxl-openpose-inpaint-scene3-api.json"

POSITIVE = (
    "A photorealistic ordinary Polish apartment-block administrator about 65 years old, "
    "standing naturally in the right side of the existing narrow basement corridor, about "
    "one to one and a half meters in front of the wooden door. Full body visible from head "
    "to shoes, correctly scaled to the corridor perspective. Short thinning grey hair, "
    "rectangular reading glasses, weathered natural face, average build, slightly stooped "
    "posture. Worn dark navy work jacket over a charcoal knitted sweater, dark trousers, "
    "ordinary black shoes. His head is slightly lowered and he looks down at several old "
    "yellowed technical building plans held with BOTH HANDS in front of his lower chest and "
    "stomach. Both forearms are visible and bent inward toward the plans. The papers are "
    "creased, aged and believable, not blank white sheets. He is physically inside the same "
    "basement, not pasted into it. Cold overhead fluorescent light from the existing ceiling "
    "fixture illuminates the top of his grey hair, shoulders and jacket. Natural darker fill "
    "on the lower body, subtle contact shadow under both shoes and a soft believable shadow "
    "on the nearby wall. Same camera perspective, same lens, same depth of field and same "
    "grain as the existing basement photograph. Documentary realism, restrained expression, "
    "ordinary building worker, subtle psychological thriller atmosphere."
)

NEGATIVE = (
    "cutout, pasted person, sticker, collage, photomontage, studio portrait, studio backdrop, "
    "grey background, white background, halo around body, rim light, beauty lighting, softbox, "
    "floating person, oversized person, tiny person, wrong perspective, fisheye person, "
    "hands in pockets, arms hanging straight down, empty hands, no papers, clipboard, laptop, "
    "phone, extra person, duplicate person, bad hands, extra fingers, missing fingers, fused "
    "arms, deformed anatomy, cropped feet, cropped head, police uniform, security uniform, "
    "business suit, cinematic hero, CGI, illustration, painting, text, watermark"
)

# OpenPose-like limb palette. Xinsir's SDXL OpenPose model benefits from thick lines.
LIMBS: list[tuple[str, str, tuple[int, int, int]]] = [
    ("head", "neck", (255, 0, 0)),
    ("neck", "l_shoulder", (255, 85, 0)),
    ("l_shoulder", "l_elbow", (255, 170, 0)),
    ("l_elbow", "l_wrist", (255, 255, 0)),
    ("neck", "r_shoulder", (170, 255, 0)),
    ("r_shoulder", "r_elbow", (85, 255, 0)),
    ("r_elbow", "r_wrist", (0, 255, 0)),
    ("neck", "mid_hip", (0, 255, 85)),
    ("mid_hip", "l_hip", (0, 255, 170)),
    ("l_hip", "l_knee", (0, 255, 255)),
    ("l_knee", "l_ankle", (0, 170, 255)),
    ("mid_hip", "r_hip", (0, 85, 255)),
    ("r_hip", "r_knee", (0, 0, 255)),
    ("r_knee", "r_ankle", (85, 0, 255)),
    ("l_shoulder", "r_shoulder", (170, 0, 255)),
    ("l_hip", "r_hip", (255, 0, 255)),
]


def validate_nodes(base_url: str) -> None:
    required = {
        "LoadImage",
        "CheckpointLoaderSimple",
        "CLIPTextEncode",
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
        raise RuntimeError("ComfyUI nie ma wymaganych node'ów: " + ", ".join(missing))


def pose_points(width: int, height: int) -> dict[str, tuple[int, int]]:
    # Coordinates are normalized against our 768x1344 master. They deliberately
    # place the man on the right wall at roughly the same scale as variant H,
    # but both arms are forced inward to hold technical plans.
    normalized = {
        "head": (0.790, 0.305),
        "neck": (0.805, 0.375),
        "l_shoulder": (0.735, 0.395),
        "r_shoulder": (0.875, 0.395),
        "l_elbow": (0.755, 0.495),
        "r_elbow": (0.850, 0.495),
        "l_wrist": (0.785, 0.555),
        "r_wrist": (0.820, 0.555),
        "mid_hip": (0.810, 0.610),
        "l_hip": (0.775, 0.610),
        "r_hip": (0.845, 0.610),
        "l_knee": (0.770, 0.745),
        "r_knee": (0.845, 0.745),
        "l_ankle": (0.765, 0.875),
        "r_ankle": (0.850, 0.875),
    }
    return {
        key: (int(round(x * width)), int(round(y * height)))
        for key, (x, y) in normalized.items()
    }


def make_pose_image(width: int, height: int, target: Path) -> dict[str, tuple[int, int]]:
    points = pose_points(width, height)
    image = Image.new("RGB", (width, height), (0, 0, 0))
    draw = ImageDraw.Draw(image)
    line_width = max(12, int(round(width * 0.020)))
    joint_radius = max(8, int(round(width * 0.013)))

    for a, b, color in LIMBS:
        draw.line([points[a], points[b]], fill=color, width=line_width)
    for index, point in enumerate(points.values()):
        hue = (index * 41) % 255
        color = (255 - hue, 80 + (hue // 2), hue)
        x, y = point
        draw.ellipse(
            [x - joint_radius, y - joint_radius, x + joint_radius, y + joint_radius],
            fill=color,
        )

    # A small head circle makes head scale explicit without face-keypoint noise.
    hx, hy = points["head"]
    head_r = max(20, int(round(width * 0.034)))
    draw.ellipse([hx - head_r, hy - head_r, hx + head_r, hy + head_r], outline=(255, 255, 255), width=5)

    target.parent.mkdir(parents=True, exist_ok=True)
    image.save(target, format="PNG", optimize=True)
    return points


def make_body_mask(
    width: int,
    height: int,
    points: dict[str, tuple[int, int]],
    mask_target: Path,
) -> Image.Image:
    # White means editable for our local compositor. Build a body-shaped region
    # around the controlled pose, leaving enough nearby wall/floor for real shadows.
    mask = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(mask)

    body_width = max(105, int(round(width * 0.17)))
    limb_width = max(80, int(round(width * 0.12)))

    # Torso and head envelope.
    hx, hy = points["head"]
    draw.ellipse([hx - 70, hy - 75, hx + 70, hy + 80], fill=255)
    draw.line(
        [points["neck"], points["mid_hip"]],
        fill=255,
        width=body_width,
    )

    # Arms, including the central plans area.
    for chain in (
        ("l_shoulder", "l_elbow", "l_wrist"),
        ("r_shoulder", "r_elbow", "r_wrist"),
        ("l_hip", "l_knee", "l_ankle"),
        ("r_hip", "r_knee", "r_ankle"),
    ):
        draw.line([points[k] for k in chain], fill=255, width=limb_width)

    lwx, lwy = points["l_wrist"]
    rwx, rwy = points["r_wrist"]
    draw.rounded_rectangle(
        [min(lwx, rwx) - 75, min(lwy, rwy) - 55, max(lwx, rwx) + 75, max(lwy, rwy) + 85],
        radius=25,
        fill=255,
    )

    # Floor/contact-shadow allowance below shoes and a little wall around silhouette.
    lax, lay = points["l_ankle"]
    rax, ray = points["r_ankle"]
    draw.ellipse(
        [min(lax, rax) - 75, min(lay, ray) - 30, max(lax, rax) + 90, max(lay, ray) + 70],
        fill=255,
    )

    mask = mask.filter(ImageFilter.GaussianBlur(radius=10))
    mask_target.parent.mkdir(parents=True, exist_ok=True)
    mask.save(mask_target, format="PNG", optimize=True)
    return mask


def make_masked_reference(reference: Path, mask: Image.Image, target: Path) -> tuple[int, int]:
    with Image.open(reference) as source:
        image = source.convert("RGBA")
    if image.size != mask.size:
        mask = mask.resize(image.size, Image.Resampling.LANCZOS)
    # ComfyUI LoadImage returns MASK from transparency; alpha=0 means editable.
    alpha = Image.eval(mask, lambda p: 255 - p)
    image.putalpha(alpha)
    target.parent.mkdir(parents=True, exist_ok=True)
    image.save(target, format="PNG", optimize=True)
    return image.width, image.height


def composite_with_mask(
    reference: Path,
    edited: Path,
    mask: Image.Image,
    target: Path,
) -> None:
    with Image.open(reference) as source:
        base = source.convert("RGB")
    with Image.open(edited) as source:
        result = source.convert("RGB").resize(base.size, Image.Resampling.LANCZOS)
    if mask.size != base.size:
        mask = mask.resize(base.size, Image.Resampling.LANCZOS)
    # Keep original pixels everywhere except the pose-shaped integration zone.
    final = Image.composite(result, base, mask)
    target.parent.mkdir(parents=True, exist_ok=True)
    final.save(target, format="PNG", optimize=True)


def add_label(image: Image.Image, text: str) -> Image.Image:
    out = image.convert("RGB")
    draw = ImageDraw.Draw(out)
    font = ImageFont.load_default()
    draw.rectangle([0, 0, out.width, 28], fill=(0, 0, 0))
    draw.text((8, 8), text, fill=(255, 255, 255), font=font)
    return out


def make_comparison(master: Path, pose: Path, final: Path, target: Path) -> None:
    panels: list[Image.Image] = []
    for path, label in ((master, "MASTER"), (pose, "OPENPOSE CONTROL"), (final, "OPENPOSE INPAINT")):
        with Image.open(path) as src:
            img = src.convert("RGB")
        thumb_h = 900
        thumb_w = int(round(img.width * thumb_h / img.height))
        img = img.resize((thumb_w, thumb_h), Image.Resampling.LANCZOS)
        panels.append(add_label(img, label))
    canvas = Image.new("RGB", (sum(p.width for p in panels), max(p.height for p in panels)), (0, 0, 0))
    x = 0
    for panel in panels:
        canvas.paste(panel, (x, 0))
        x += panel.width
    target.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(target, format="JPEG", quality=92, optimize=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("short_file", nargs="?", default="shorts/001-drzwi-0.yaml")
    parser.add_argument("--reference", required=True)
    parser.add_argument("--checkpoint", default="sd_xl_base_1.0.safetensors")
    parser.add_argument("--controlnet", default="xinsir-controlnet-openpose-sdxl-1.0.safetensors")
    parser.add_argument("--seed", type=int, default=1700701)
    parser.add_argument("--steps", type=int, default=32)
    parser.add_argument("--cfg", type=float, default=6.3)
    parser.add_argument("--control-strength", type=float, default=1.0)
    parser.add_argument("--control-end", type=float, default=0.85)
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

    with Image.open(reference) as src:
        width, height = src.size

    out_dir = short_output_dir(short) / "compare" / "scene-03" / "sdxl-openpose"
    work_dir = out_dir / "work"
    out_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    pose_path = out_dir / "openpose-control.png"
    mask_path = out_dir / "integration-mask.png"
    masked_reference = work_dir / "scene-01-openpose-inpaint-reference.png"
    raw_target = work_dir / "openpose-inpaint-raw.png"
    final_target = out_dir / "openpose-inpaint-final.png"
    comparison = out_dir / "comparison-openpose.jpg"

    points = make_pose_image(width, height, pose_path)
    edit_mask = make_body_mask(width, height, points, mask_path)
    make_masked_reference(reference, edit_mask, masked_reference)

    uploaded_ref = upload_input_image(
        base_url,
        masked_reference,
        f"csp_{short['id']}_scene03_openpose_inpaint_ref.png",
    )
    uploaded_pose = upload_input_image(
        base_url,
        pose_path,
        f"csp_{short['id']}_scene03_openpose_control.png",
    )

    workflow: dict[str, Any] = json.loads(WORKFLOW.read_text(encoding="utf-8"))
    workflow["1"]["inputs"]["image"] = uploaded_ref
    workflow["2"]["inputs"]["image"] = uploaded_pose
    workflow["3"]["inputs"]["ckpt_name"] = args.checkpoint
    workflow["4"]["inputs"]["text"] = POSITIVE
    workflow["5"]["inputs"]["text"] = NEGATIVE
    workflow["6"]["inputs"]["control_net_name"] = args.controlnet
    workflow["7"]["inputs"]["strength"] = float(args.control_strength)
    workflow["7"]["inputs"]["end_percent"] = float(args.control_end)
    workflow["9"]["inputs"]["seed"] = int(args.seed)
    workflow["9"]["inputs"]["steps"] = int(args.steps)
    workflow["9"]["inputs"]["cfg"] = float(args.cfg)
    workflow["11"]["inputs"]["filename_prefix"] = f"csp_{short['id']}_scene03_openpose"

    print(
        "OPENPOSE TEST scene 3: "
        f"{width}x{height}, steps={args.steps}, cfg={args.cfg}, "
        f"CN={args.control_strength}, end={args.control_end}, seed={args.seed}"
    )
    render_workflow(
        base_url=base_url,
        comfy=comfy,
        workflow=copy.deepcopy(workflow),
        save_node="11",
        target=raw_target,
    )

    composite_with_mask(reference, raw_target, edit_mask, final_target)
    make_comparison(reference, pose_path, final_target, comparison)

    print(f"POSE  {pose_path}")
    print(f"MASK  {mask_path}")
    print(f"RAW   {raw_target}")
    print(f"FINAL {final_target}")
    print(f"COMPARE {comparison}")
    free_comfy_memory(base_url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
