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
from PIL import Image

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
    response = requests.post(
        f"{base_url}/prompt", json={"prompt": workflow}, timeout=30
    )
    response.raise_for_status()
    payload = response.json()
    prompt_id = payload.get("prompt_id")
    if not prompt_id:
        raise RuntimeError(
            "ComfyUI odrzucił workflow: "
            + json.dumps(payload.get("node_errors", payload), ensure_ascii=False)
        )
    return str(prompt_id)


def wait_history(
    base_url: str, prompt_id: str, timeout: int, poll: int
) -> dict[str, Any]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        response = requests.get(
            f"{base_url}/history/{prompt_id}", timeout=30
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
            return history
        time.sleep(poll)
    raise TimeoutError(f"Timeout ComfyUI dla prompt_id={prompt_id}")


def download_first_image(
    base_url: str, history: dict[str, Any], save_node: str, target: Path
) -> None:
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
    response = requests.get(
        f"{base_url}/view", params=params, timeout=120
    )
    response.raise_for_status()
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
    if subfolder:
        return f"{subfolder}/{name}"
    return name


def apply_binding(
    workflow: dict[str, Any], binding: dict[str, str] | None, value: Any
) -> None:
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


def scene_seed(short: dict[str, Any], scene_id: int, base_seed: int) -> int:
    continuity = short.get("continuity") or {}
    mode = str(continuity.get("seed_mode", "per_scene")).strip().lower()
    if mode == "shared":
        return base_seed
    return base_seed + scene_id


def normalized_crop_box(
    crop: Any, width: int, height: int
) -> tuple[int, int, int, int]:
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


def prepare_control_image(
    reference: Path,
    target: Path,
    crop: Any,
    width: int,
    height: int,
) -> None:
    with Image.open(reference) as source:
        image = source.convert("RGB")
        box = normalized_crop_box(crop, image.width, image.height)
        image = image.crop(box)
        image = image.resize((width, height), Image.Resampling.LANCZOS)
        target.parent.mkdir(parents=True, exist_ok=True)
        image.save(target, format="PNG", optimize=True)


def free_comfy_memory(base_url: str) -> None:
    try:
        response = requests.post(
            f"{base_url}/free",
            json={"unload_models": True, "free_memory": True},
            timeout=30,
        )
        response.raise_for_status()
        print("COMFY: requested model unload / VRAM release")
    except Exception as exc:  # image files are already safe; don't hide that fact
        print(f"WARN: nie udało się zwolnić pamięci ComfyUI: {exc}")


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
    config = load_yaml("config/models.yaml")
    comfy = config["comfyui"]
    base_url = os.getenv("CSP_COMFY_URL", comfy["base_url"]).rstrip("/")
    model_cfg = config["image_models"][short["image_model"]]

    workflow_path = ROOT / model_cfg["workflow"]
    if not workflow_path.exists():
        raise FileNotFoundError(f"Brak workflow ComfyUI: {workflow_path}")

    bindings = model_cfg.get("bindings") or {}
    required_bindings = {"prompt", "seed", "width", "height", "steps", "save_prefix"}
    missing = sorted(required_bindings - set(bindings))
    if missing:
        raise RuntimeError(
            f"Model {short['image_model']} nie ma kompletu bindings: {', '.join(missing)}"
        )

    workflow_template = json.loads(workflow_path.read_text(encoding="utf-8"))

    control_template: dict[str, Any] | None = None
    control_bindings = model_cfg.get("control_bindings") or {}
    control_workflow_raw = model_cfg.get("control_workflow")
    if control_workflow_raw:
        control_path = ROOT / str(control_workflow_raw)
        if not control_path.exists():
            raise FileNotFoundError(f"Brak ControlNet workflow: {control_path}")
        control_template = json.loads(control_path.read_text(encoding="utf-8"))

    output_dir = short_output_dir(short) / "images"
    output_dir.mkdir(parents=True, exist_ok=True)
    control_dir = short_output_dir(short) / "control"
    control_dir.mkdir(parents=True, exist_ok=True)

    wait_for_comfy(base_url)

    # 8 GB VRAM: tylko jedna scena jest jednocześnie w kolejce.
    base_seed = deterministic_seed(short)
    style = str(short.get("visual_style", "")).strip()
    width = int(model_cfg.get("width", 768))
    height = int(model_cfg.get("height", 1344))
    steps = int(model_cfg.get("steps", 8))
    control_steps = int(model_cfg.get("control_steps", max(steps, 9)))
    models = model_cfg.get("models") or {}
    megapixels = (width * height) / 1_000_000

    save_node = str(bindings["save_prefix"]["node"])

    for scene in short["scenes"]:
        scene_id = int(scene["id"])
        target = output_dir / f"scene-{scene_id:02d}.png"
        if target.exists() and not args.force:
            print(f"SKIP {target.name}")
            continue

        continuity = continuity_prompt(short, scene)
        final_prompt = ". ".join(
            part
            for part in (style, continuity, str(scene["prompt"]).strip())
            if part
        )
        seed = scene_seed(short, scene_id, base_seed)
        control = scene.get("control") or {}

        if control:
            if control_template is None:
                raise RuntimeError(
                    f"Scena {scene_id} wymaga ControlNet, ale model nie ma control_workflow"
                )
            required_control = {
                "prompt",
                "seed",
                "steps",
                "unet",
                "clip",
                "vae",
                "patch",
                "reference_image",
                "strength",
                "canny_low",
                "canny_high",
                "megapixels",
                "save_prefix",
            }
            missing_control = sorted(required_control - set(control_bindings))
            if missing_control:
                raise RuntimeError(
                    "Brak control bindings: " + ", ".join(missing_control)
                )

            ref_scene = int(control.get("reference_scene", 0))
            if ref_scene < 1 or ref_scene >= scene_id:
                raise ValueError(
                    f"Scena {scene_id}: reference_scene musi wskazywać wcześniejszą scenę"
                )
            reference = output_dir / f"scene-{ref_scene:02d}.png"
            if not reference.exists():
                raise FileNotFoundError(
                    f"Scena {scene_id}: brak obrazu referencyjnego {reference}"
                )

            control_image = control_dir / f"scene-{scene_id:02d}-from-{ref_scene:02d}.png"
            prepare_control_image(
                reference,
                control_image,
                control.get("crop"),
                width,
                height,
            )
            remote_name = f"csp_{short['id']}_control_{scene_id:02d}.png"
            uploaded = upload_input_image(base_url, control_image, remote_name)

            workflow = copy.deepcopy(control_template)
            apply_binding(workflow, control_bindings.get("prompt"), final_prompt)
            apply_binding(workflow, control_bindings.get("seed"), seed)
            apply_binding(workflow, control_bindings.get("steps"), control_steps)
            apply_binding(workflow, control_bindings.get("unet"), models.get("unet"))
            apply_binding(workflow, control_bindings.get("clip"), models.get("clip"))
            apply_binding(workflow, control_bindings.get("vae"), models.get("vae"))
            apply_binding(workflow, control_bindings.get("patch"), models.get("patch"))
            apply_binding(workflow, control_bindings.get("reference_image"), uploaded)
            apply_binding(
                workflow,
                control_bindings.get("strength"),
                float(control.get("strength", 0.9)),
            )
            apply_binding(
                workflow,
                control_bindings.get("canny_low"),
                float(control.get("canny_low", 0.1)),
            )
            apply_binding(
                workflow,
                control_bindings.get("canny_high"),
                float(control.get("canny_high", 0.32)),
            )
            apply_binding(
                workflow,
                control_bindings.get("megapixels"),
                megapixels,
            )
            apply_binding(
                workflow,
                control_bindings.get("save_prefix"),
                f"csp_{short['id']}_scene_{scene_id:02d}_control",
            )
            control_save_node = str(control_bindings["save_prefix"]["node"])
            print(
                f"GENERATE scene {scene_id}/8 via Z-Image ControlNet "
                f"ref={ref_scene}, strength={float(control.get('strength', 0.9)):.2f}, "
                f"seed={seed}"
            )
            render_workflow(
                base_url=base_url,
                comfy=comfy,
                workflow=workflow,
                save_node=control_save_node,
                target=target,
            )
        else:
            workflow = copy.deepcopy(workflow_template)
            apply_binding(workflow, bindings.get("prompt"), final_prompt)
            apply_binding(workflow, bindings.get("seed"), seed)
            apply_binding(workflow, bindings.get("steps"), steps)
            apply_binding(workflow, bindings.get("width"), width)
            apply_binding(workflow, bindings.get("height"), height)
            apply_binding(workflow, bindings.get("unet"), models.get("unet"))
            apply_binding(workflow, bindings.get("clip"), models.get("clip"))
            apply_binding(workflow, bindings.get("vae"), models.get("vae"))
            apply_binding(
                workflow,
                bindings.get("save_prefix"),
                f"csp_{short['id']}_scene_{scene_id:02d}",
            )
            print(
                f"GENERATE scene {scene_id}/8 via {short['image_model']} "
                f"({width}x{height}, {steps} steps, seed={seed})"
            )
            render_workflow(
                base_url=base_url,
                comfy=comfy,
                workflow=workflow,
                save_node=save_node,
                target=target,
            )

        print(f"SAVED {target}")

    if comfy.get("free_after_images", True):
        free_comfy_memory(base_url)


if __name__ == "__main__":
    main()
