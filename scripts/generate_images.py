from __future__ import annotations

import argparse
import copy
import json
import random
import time
from pathlib import Path

import requests

from common import ROOT, load_yaml, short_output_dir


def wait_for_comfy(base_url: str, timeout: int = 30) -> None:
    deadline = time.time() + timeout
    last_error = None
    while time.time() < deadline:
        try:
            r = requests.get(f"{base_url}/system_stats", timeout=5)
            if r.ok:
                return
        except Exception as exc:
            last_error = exc
        time.sleep(2)
    raise RuntimeError(f"ComfyUI nie odpowiada: {last_error}")


def submit_prompt(base_url: str, workflow: dict) -> str:
    r = requests.post(f"{base_url}/prompt", json={"prompt": workflow}, timeout=30)
    r.raise_for_status()
    return r.json()["prompt_id"]


def wait_history(base_url: str, prompt_id: str, timeout: int, poll: int) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = requests.get(f"{base_url}/history/{prompt_id}", timeout=30)
        r.raise_for_status()
        payload = r.json()
        if prompt_id in payload:
            return payload[prompt_id]
        time.sleep(poll)
    raise TimeoutError(f"Timeout ComfyUI dla prompt_id={prompt_id}")


def download_first_image(base_url: str, history: dict, save_node: str, target: Path) -> None:
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
    r = requests.get(f"{base_url}/view", params=params, timeout=120)
    r.raise_for_status()
    target.write_bytes(r.content)


def set_prompt(workflow: dict, node_id: str, prompt: str) -> None:
    node = workflow[str(node_id)]
    inputs = node.setdefault("inputs", {})
    for key in ("text", "prompt", "positive"):
        if key in inputs:
            inputs[key] = prompt
            return
    inputs["text"] = prompt


def set_seed(workflow: dict, node_id: str, seed: int) -> None:
    node = workflow[str(node_id)]
    inputs = node.setdefault("inputs", {})
    for key in ("seed", "noise_seed"):
        if key in inputs:
            inputs[key] = seed
            return
    inputs["seed"] = seed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("short_file")
    args = parser.parse_args()

    short = load_yaml(args.short_file)
    config = load_yaml("config/models.yaml")
    comfy = config["comfyui"]
    model_cfg = config["image_models"][short["image_model"]]

    workflow_path = ROOT / model_cfg["workflow"]
    if not workflow_path.exists():
        raise FileNotFoundError(
            f"Brak workflow ComfyUI: {workflow_path}. Wyeksportuj workflow w formacie API."
        )

    workflow_template = json.loads(workflow_path.read_text(encoding="utf-8"))
    output_dir = short_output_dir(short) / "images"
    output_dir.mkdir(parents=True, exist_ok=True)

    wait_for_comfy(comfy["base_url"])

    # 8 GB VRAM: jedna scena naraz, jeden workflow w kolejce.
    base_seed = random.randint(1, 2_000_000_000)
    style = short.get("visual_style", "")

    for scene in short["scenes"]:
        scene_id = int(scene["id"])
        target = output_dir / f"scene-{scene_id:02d}.png"
        if target.exists():
            print(f"SKIP {target.name}")
            continue

        workflow = copy.deepcopy(workflow_template)
        final_prompt = f"{style}. {scene['prompt']}"
        set_prompt(workflow, model_cfg["prompt_node"], final_prompt)
        set_seed(workflow, model_cfg["seed_node"], base_seed + scene_id)

        print(f"GENERATE scene {scene_id}/8 via {short['image_model']}")
        prompt_id = submit_prompt(comfy["base_url"], workflow)
        history = wait_history(
            comfy["base_url"], prompt_id,
            timeout=int(comfy.get("timeout_seconds", 900)),
            poll=int(comfy.get("poll_seconds", 2)),
        )
        download_first_image(
            comfy["base_url"], history, model_cfg["save_node"], target
        )
        print(f"SAVED {target}")


if __name__ == "__main__":
    main()
