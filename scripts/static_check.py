from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import yaml

from common import ROOT, load_yaml


def fail(message: str) -> None:
    raise SystemExit(f"STATIC CHECK FAILED: {message}")


def check_python() -> None:
    files = sorted((ROOT / "scripts").glob("*.py")) + sorted((ROOT / "tools").glob("*.py"))
    if not files:
        fail("brak plików Python")
    for path in files:
        try:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            fail(f"Python syntax: {path}: {exc}")
    print(f"[OK] Python syntax: {len(files)} files")


def check_yaml() -> None:
    files = [ROOT / "config" / "models.yaml"] + sorted((ROOT / "shorts").glob("*.yaml"))
    for path in files:
        try:
            yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception as exc:
            fail(f"YAML: {path}: {exc}")
    print(f"[OK] YAML: {len(files)} files")


def check_comfy_workflow() -> None:
    config = load_yaml("config/models.yaml")
    model = config["image_models"]["z-image-turbo"]
    path = ROOT / model["workflow"]
    try:
        graph = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        fail(f"ComfyUI JSON: {exc}")

    required_classes = {
        "UNETLoader",
        "CLIPLoader",
        "VAELoader",
        "CLIPTextEncode",
        "ConditioningZeroOut",
        "EmptySD3LatentImage",
        "ModelSamplingAuraFlow",
        "KSampler",
        "VAEDecode",
        "SaveImage",
    }
    classes = {str(node.get("class_type")) for node in graph.values()}
    missing = required_classes - classes
    if missing:
        fail("ComfyUI graph missing classes: " + ", ".join(sorted(missing)))

    for name, binding in (model.get("bindings") or {}).items():
        node = str(binding["node"])
        input_name = str(binding["input"])
        if node not in graph:
            fail(f"binding {name}: missing node {node}")
        if input_name not in graph[node].get("inputs", {}):
            fail(f"binding {name}: missing input {node}.{input_name}")

    if graph["3"]["inputs"].get("steps") != 8:
        fail("Z-Image smoke workflow powinien domyślnie używać 8 kroków")
    print("[OK] ComfyUI Z-Image graph + bindings")


def check_short() -> None:
    target = ROOT / "shorts" / "001-drzwi-0.yaml"
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "validate_short.py"), str(target)],
        capture_output=True,
        text=True,
    )
    print(result.stdout, end="")
    if result.returncode != 0:
        print(result.stderr, end="")
        fail("smoke short validation failed")
    print("[OK] smoke short")


def check_workflow_references() -> None:
    workflow = (ROOT / ".github" / "workflows" / "build-short.yml").read_text(encoding="utf-8")
    required_scripts = [
        "scripts/preflight.py",
        "scripts/validate_short.py",
        "scripts/generate_images.py",
        "scripts/generate_tts.py",
        "scripts/transcribe.py",
        "scripts/sound_design.py",
        "scripts/render.py",
    ]
    for script in required_scripts:
        if script not in workflow:
            fail(f"Build workflow nie wywołuje {script}")
        if not (ROOT / script).exists():
            fail(f"Build workflow wskazuje brakujący plik {script}")
    print("[OK] Build workflow references")


def main() -> None:
    check_python()
    check_yaml()
    check_comfy_workflow()
    check_short()
    check_workflow_references()
    print("STATIC CHECKS OK")


if __name__ == "__main__":
    main()
