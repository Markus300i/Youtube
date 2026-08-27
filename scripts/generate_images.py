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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("short_file")
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
    output_dir = short_output_dir(short) / "images"
    output_dir.mkdir(parents=True, exist_ok=True)

    wait_for_comfy(base_url)

    # 8 GB VRAM: tylko jedna scena jest jednocześnie w kolejce.
    base_seed = deterministic_seed(short)
    style = str(short.get("visual_style", "")).strip()
    width = int(model_cfg.get("width", 768))
    height = int(model_cfg.get("height", 1344))
    steps = int(model_cfg.get("steps", 8))
    models = model_cfg.get("models") or {}

    save_node = str(bindings["save_prefix"]["node"])

    for scene in short["scenes"]:
        scene_id = int(scene["id"])
        target = output_dir / f"scene-{scene_id:02d}.png"
        if target.exists():
            print(f"SKIP {target.name}")
            continue

        workflow = copy.deepcopy(workflow_template)
        final_prompt = ". ".join(
            part for part in (style, str(scene["prompt"]).strip()) if part
        )

        apply_binding(workflow, bindings.get("prompt"), final_prompt)
        apply_binding(workflow, bindings.get("seed"), base_seed + scene_id)
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
            f"({width}x{height}, {steps} steps, seed={base_seed + scene_id})"
        )
        prompt_id = submit_prompt(base_url, workflow)
        history = wait_history(
            base_url,
            prompt_id,
            timeout=int(comfy.get("timeout_seconds", 1200)),
            poll=int(comfy.get("poll_seconds", 2)),
        )
        download_first_image(base_url, history, save_node, target)
        print(f"SAVED {target}")

    if comfy.get("free_after_images", True):
        free_comfy_memory(base_url)


if __name__ == "__main__":
    main()
