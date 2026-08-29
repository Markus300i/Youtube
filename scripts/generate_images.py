from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

import requests
from PIL import Image, ImageDraw, ImageFilter

from common import ROOT, load_yaml, short_output_dir


def wait_for_comfy(base_url: str, timeout: int = 30) -> None:
    deadline = time.time() + timeout
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            response = requests.get(f"{base_url}/system_stats", timeout=5)
            if response.ok:
                return
        except Exception as exc:  # pragma: no cover - depends on local ComfyUI
            last_error = exc
        time.sleep(2)
    raise RuntimeError(f"ComfyUI nie odpowiada: {last_error}")


def submit_prompt(base_url: str, workflow: dict[str, Any]) -> str:
    response = requests.post(f"{base_url}/prompt", json={"prompt": workflow}, timeout=30)
    response.raise_for_status()
    payload = response.json()
    prompt_id = payload.get("prompt_id")
    if not prompt_id:
        raise RuntimeError(
            "ComfyUI odrzucił workflow: "
            + json.dumps(payload.get("node_errors", payload), ensure_ascii=False)
        )
    return str(prompt_id)


def wait_history(base_url: str, prompt_id: str, timeout: int, poll: int) -> dict[str, Any]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        response = requests.get(f"{base_url}/history/{prompt_id}", timeout=30)
        response.raise_for_status()
        payload = response.json()
        if prompt_id in payload:
            history = payload[prompt_id]
            status = history.get("status", {})
            if status.get("status_str") == "error":
                raise RuntimeError(
                    "Błąd wykonania ComfyUI: " + json.dumps(status, ensure_ascii=False)
                )
            return history
        time.sleep(poll)
    raise TimeoutError(f"Timeout ComfyUI dla prompt_id={prompt_id}")


def download_first_image(base_url: str, history: dict[str, Any], save_node: str, target: Path) -> None:
    outputs = history.get("outputs", {})
    node = outputs.get(str(save_node)) or outputs.get(save_node)
    if not node:
        raise RuntimeError(f"Brak outputu z węzła SaveImage {save_node}")
    images = node.get("images") or []
    if not images:
        raise RuntimeError("ComfyUI nie zwrócił obrazu")

    image = images[0]
    params = {
        "filename": image["filename"],
        "subfolder": image.get("subfolder", ""),
        "type": image.get("type", "output"),
    }
    response = requests.get(f"{base_url}/view", params=params, timeout=120)
    response.raise_for_status()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(response.content)


def upload_input_image(base_url: str, path: Path, remote_name: str) -> str:
    with path.open("rb") as handle:
        response = requests.post(
            f"{base_url}/upload/image",
            files={"image": (remote_name, handle, "image/png")},
            data={"type": "input", "overwrite": "true"},
            timeout=120,
        )
    response.raise_for_status()
    payload = response.json()
    name = str(payload.get("name") or remote_name)
    subfolder = str(payload.get("subfolder") or "").strip("/\\")
    return f"{subfolder}/{name}" if subfolder else name


def apply_binding(workflow: dict[str, Any], binding: dict[str, str] | None, value: Any) -> None:
    if not binding:
        return
    node_id = str(binding["node"])
    input_name = str(binding["input"])
    if node_id not in workflow:
        raise KeyError(f"Workflow nie ma węzła {node_id}")
    workflow[node_id].setdefault("inputs", {})[input_name] = value


def deterministic_seed(short: dict[str, Any]) -> int:
    if short.get("seed") is not None:
        return int(short["seed"])
    raw = f"{short['id']}:{short['title']}".encode("utf-8")
    return int.from_bytes(hashlib.sha256(raw).digest()[:8], "big") % 2_000_000_000


def continuity_prompt(short: dict[str, Any], scene: dict[str, Any]) -> str:
    continuity = short.get("continuity") or {}
    if not isinstance(continuity, dict):
        return ""

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
            key = str(ref)
            value = anchors.get(key)
            if value:
                parts.append(str(value).strip())
            else:
                print(
                    f"WARN: scena {scene.get('id')} odwołuje się do "
                    f"nieznanego continuity anchor '{key}'"
                )

    return ". ".join(part for part in parts if part)


def scene_seed(short: dict[str, Any], scene: dict[str, Any], base_seed: int) -> int:
    if scene.get("seed") is not None:
        return int(scene["seed"])
    scene_id = int(scene["id"])
    continuity = short.get("continuity") or {}
    mode = str(continuity.get("seed_mode", "per_scene")).strip().lower()
    return base_seed if mode == "shared" else base_seed + scene_id


def expected_scene_paths(short: dict[str, Any], output_dir: Path) -> list[Path]:
    return [output_dir / f"scene-{int(scene['id']):02d}.png" for scene in short.get("scenes", [])]


def complete_scene_images(short: dict[str, Any], output_dir: Path) -> bool:
    paths = expected_scene_paths(short, output_dir)
    return bool(paths) and all(path.is_file() and path.stat().st_size > 0 for path in paths)


def normalized_crop_box(crop: Any, width: int, height: int) -> tuple[int, int, int, int]:
    if not isinstance(crop, (list, tuple)) or len(crop) != 4:
        return (0, 0, width, height)
    values = [float(item) for item in crop]
    left = max(0.0, min(0.98, values[0]))
    top = max(0.0, min(0.98, values[1]))
    right = max(left + 0.01, min(1.0, values[2]))
    bottom = max(top + 0.01, min(1.0, values[3]))
    return (
        int(round(left * width)),
        int(round(top * height)),
        int(round(right * width)),
        int(round(bottom * height)),
    )


def prepare_reference_image(reference: Path, target: Path, crop: Any, width: int, height: int) -> None:
    with Image.open(reference) as source:
        image = source.convert("RGB")
        image = image.crop(normalized_crop_box(crop, image.width, image.height))
        image = image.resize((width, height), Image.Resampling.LANCZOS)
        target.parent.mkdir(parents=True, exist_ok=True)
        image.save(target, format="PNG", optimize=True)


def mask_from_rects(rects: Any, width: int, height: int, feather: float) -> Image.Image:
    mask = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(mask)
    if isinstance(rects, (list, tuple)):
        for rect in rects:
            if not isinstance(rect, (list, tuple)) or len(rect) != 4:
                continue
            draw.rectangle(normalized_crop_box(rect, width, height), fill=255)
    radius = max(0.0, float(feather)) * min(width, height)
    return mask.filter(ImageFilter.GaussianBlur(radius=radius)) if radius > 0 else mask


def composite_edit(
    base_path: Path,
    edited_path: Path,
    target: Path,
    edit_rects: Any,
    preserve_rects: Any,
    feather: float,
    width: int,
    height: int,
) -> None:
    with Image.open(base_path) as base_source:
        base = base_source.convert("RGB").resize((width, height), Image.Resampling.LANCZOS)
    with Image.open(edited_path) as edited_source:
        edited = edited_source.convert("RGB").resize((width, height), Image.Resampling.LANCZOS)

    if edit_rects:
        result = Image.composite(edited, base, mask_from_rects(edit_rects, width, height, feather))
    else:
        result = edited

    if preserve_rects:
        result = Image.composite(
            base,
            result,
            mask_from_rects(preserve_rects, width, height, feather),
        )

    target.parent.mkdir(parents=True, exist_ok=True)
    result.save(target, format="PNG", optimize=True)


def render_workflow(
    *,
    base_url: str,
    comfy: dict[str, Any],
    workflow: dict[str, Any],
    save_node: str,
    target: Path,
) -> None:
    prompt_id = submit_prompt(base_url, workflow)
    history = wait_history(
        base_url,
        prompt_id,
        timeout=int(comfy.get("timeout_seconds", 1200)),
        poll=int(comfy.get("poll_seconds", 2)),
    )
    download_first_image(base_url, history, save_node, target)


def load_workflow(model_cfg: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    workflow_path = ROOT / str(model_cfg["workflow"])
    if not workflow_path.exists():
        raise FileNotFoundError(f"Brak workflow ComfyUI: {workflow_path}")
    return json.loads(workflow_path.read_text(encoding="utf-8")), model_cfg.get("bindings") or {}


def scene_reference(output_dir: Path, scene_id: int, render_cfg: dict[str, Any]) -> Path:
    ref_scene = int(render_cfg.get("reference_scene", 0))
    if ref_scene < 1 or ref_scene >= scene_id:
        raise ValueError(f"Scena {scene_id}: reference_scene musi wskazywać wcześniejszą scenę")
    reference = output_dir / f"scene-{ref_scene:02d}.png"
    if not reference.exists():
        raise FileNotFoundError(f"Scena {scene_id}: brak obrazu referencyjnego {reference}")
    return reference


def free_comfy_memory(base_url: str) -> None:
    try:
        response = requests.post(
            f"{base_url}/free",
            json={"unload_models": True, "free_memory": True},
            timeout=30,
        )
        response.raise_for_status()
        print("COMFY: requested model unload / VRAM release")
    except Exception as exc:
        print(f"WARN: nie udało się zwolnić pamięci ComfyUI: {exc}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("short_file")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Regenerate and overwrite scene images that already exist.",
    )
    args = parser.parse_args()

    short = load_yaml(args.short_file)
    output_dir = short_output_dir(short) / "images"
    output_dir.mkdir(parents=True, exist_ok=True)

    # V1.1: manual/GPT supplied images are a first-class input. Resume must be
    # checked before model config, workflows or ComfyUI are touched.
    if not args.force and complete_scene_images(short, output_dir):
        for target in expected_scene_paths(short, output_dir):
            print(f"SKIP {target.name}")
        print(f"IMAGES READY: {len(expected_scene_paths(short, output_dir))} existing scene images; ComfyUI not required")
        return

    config = load_yaml("config/models.yaml")
    comfy = config["comfyui"]
    base_url = os.getenv("CSP_COMFY_URL", comfy["base_url"]).rstrip("/")

    base_model_cfg = config["image_models"][short["image_model"]]
    base_workflow, base_bindings = load_workflow(base_model_cfg)
    required = {"prompt", "seed", "width", "height", "steps", "save_prefix"}
    missing = sorted(required - set(base_bindings))
    if missing:
        raise RuntimeError(
            f"Model {short['image_model']} nie ma kompletu bindings: " + ", ".join(missing)
        )

    edit_dir = short_output_dir(short) / "edit"
    edit_dir.mkdir(parents=True, exist_ok=True)

    # There is at least one missing image, so generation/edit workflows may be
    # needed. Preserve the existing ComfyUI behavior for partial resumes.
    wait_for_comfy(base_url)

    base_seed = deterministic_seed(short)
    style = str(short.get("visual_style", "")).strip()
    width = int(base_model_cfg.get("width", 768))
    height = int(base_model_cfg.get("height", 1344))
    steps = int(base_model_cfg.get("steps", 8))
    base_models = base_model_cfg.get("models") or {}
    base_save_node = str(base_bindings["save_prefix"]["node"])

    flux_cfg = config["image_models"].get("flux2-klein-edit")
    flux_template: dict[str, Any] | None = None
    flux_bindings: dict[str, Any] = {}
    scene_count = len(short["scenes"])

    for scene in short["scenes"]:
        scene_id = int(scene["id"])
        target = output_dir / f"scene-{scene_id:02d}.png"
        if target.exists() and target.stat().st_size > 0 and not args.force:
            print(f"SKIP {target.name}")
            continue

        render_cfg = scene.get("render") or {}
        mode = str(render_cfg.get("mode", "generate")).strip().lower()
        continuity = continuity_prompt(short, scene)
        final_prompt = ". ".join(
            part for part in (style, continuity, str(scene["prompt"]).strip()) if part
        )
        seed = scene_seed(short, scene, base_seed)

        if mode == "crop":
            reference = scene_reference(output_dir, scene_id, render_cfg)
            prepare_reference_image(reference, target, render_cfg.get("crop"), width, height)
            print(
                f"CROP scene {scene_id}/{scene_count} from scene "
                f"{int(render_cfg['reference_scene'])} -> {width}x{height}"
            )
            print(f"SAVED {target}")
            continue

        if mode == "flux_edit":
            if not flux_cfg:
                raise RuntimeError("Brak konfiguracji image_models.flux2-klein-edit")
            if flux_template is None:
                flux_template, flux_bindings = load_workflow(flux_cfg)

            required_flux = {
                "prompt", "seed", "steps", "cfg", "unet", "clip", "vae",
                "reference_image", "megapixels", "save_prefix",
            }
            missing_flux = sorted(required_flux - set(flux_bindings))
            if missing_flux:
                raise RuntimeError("FLUX.2 edit bindings są niekompletne: " + ", ".join(missing_flux))

            reference = scene_reference(output_dir, scene_id, render_cfg)
            base_path = edit_dir / f"scene-{scene_id:02d}-base.png"
            edited_path = edit_dir / f"scene-{scene_id:02d}-raw.png"
            prepare_reference_image(reference, base_path, render_cfg.get("crop"), width, height)
            remote_name = f"csp_{short['id']}_edit_{scene_id:02d}.png"
            uploaded = upload_input_image(base_url, base_path, remote_name)

            workflow = copy.deepcopy(flux_template)
            flux_models = flux_cfg.get("models") or {}
            instruction = str(render_cfg.get("instruction") or final_prompt).strip()
            apply_binding(workflow, flux_bindings.get("prompt"), instruction)
            apply_binding(workflow, flux_bindings.get("seed"), seed + 20_000)
            apply_binding(workflow, flux_bindings.get("steps"), int(render_cfg.get("steps", flux_cfg.get("steps", 20))))
            apply_binding(workflow, flux_bindings.get("cfg"), float(render_cfg.get("cfg", flux_cfg.get("cfg", 5.0))))
            apply_binding(workflow, flux_bindings.get("unet"), flux_models.get("unet"))
            apply_binding(workflow, flux_bindings.get("clip"), flux_models.get("clip"))
            apply_binding(workflow, flux_bindings.get("vae"), flux_models.get("vae"))
            apply_binding(workflow, flux_bindings.get("reference_image"), uploaded)
            apply_binding(workflow, flux_bindings.get("megapixels"), (width * height) / 1_000_000)
            apply_binding(
                workflow,
                flux_bindings.get("save_prefix"),
                f"csp_{short['id']}_scene_{scene_id:02d}_fluxedit",
            )

            print(
                f"EDIT scene {scene_id}/{scene_count} via FLUX.2 Klein 4B "
                f"from scene {int(render_cfg['reference_scene'])}"
            )
            render_workflow(
                base_url=base_url,
                comfy=comfy,
                workflow=workflow,
                save_node=str(flux_bindings["save_prefix"]["node"]),
                target=edited_path,
            )
            composite_edit(
                base_path,
                edited_path,
                target,
                render_cfg.get("edit_rects") or render_cfg.get("mask_rects"),
                render_cfg.get("preserve_rects"),
                float(render_cfg.get("feather", 0.02)),
                width,
                height,
            )
            print(f"SAVED {target}")
            continue

        if mode != "generate":
            raise ValueError(f"Scena {scene_id}: nieobsługiwany render.mode '{mode}'")

        workflow = copy.deepcopy(base_workflow)
        apply_binding(workflow, base_bindings.get("prompt"), final_prompt)
        apply_binding(workflow, base_bindings.get("seed"), seed)
        apply_binding(workflow, base_bindings.get("steps"), steps)
        apply_binding(workflow, base_bindings.get("width"), width)
        apply_binding(workflow, base_bindings.get("height"), height)
        apply_binding(workflow, base_bindings.get("unet"), base_models.get("unet"))
        apply_binding(workflow, base_bindings.get("clip"), base_models.get("clip"))
        apply_binding(workflow, base_bindings.get("vae"), base_models.get("vae"))
        apply_binding(
            workflow,
            base_bindings.get("save_prefix"),
            f"csp_{short['id']}_scene_{scene_id:02d}",
        )
        print(
            f"GENERATE scene {scene_id}/{scene_count} via {short['image_model']} "
            f"({width}x{height}, {steps} steps, seed={seed})"
        )
        render_workflow(
            base_url=base_url,
            comfy=comfy,
            workflow=workflow,
            save_node=base_save_node,
            target=target,
        )
        print(f"SAVED {target}")

    if comfy.get("free_after_images", True):
        free_comfy_memory(base_url)


if __name__ == "__main__":
    main()
