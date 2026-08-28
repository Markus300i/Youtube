from __future__ import annotations

import ast
import json
import subprocess
import sys

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
    files = (
        [ROOT / "config" / "models.yaml"]
        + sorted((ROOT / "shorts").glob("*.yaml"))
        + sorted((ROOT / ".github" / "workflows").glob("*.yml"))
        + sorted((ROOT / ".github" / "workflows").glob("*.yaml"))
    )
    for path in files:
        try:
            yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception as exc:
            fail(f"YAML: {path}: {exc}")
    print(f"[OK] YAML: {len(files)} files")


def validate_graph(
    graph: dict,
    bindings: dict,
    required_classes: set[str],
    label: str,
) -> None:
    classes = {str(node.get("class_type")) for node in graph.values()}
    missing = required_classes - classes
    if missing:
        fail(f"{label} missing classes: " + ", ".join(sorted(missing)))

    for name, binding in bindings.items():
        node = str(binding["node"])
        input_name = str(binding["input"])
        if node not in graph:
            fail(f"{label} binding {name}: missing node {node}")
        if input_name not in graph[node].get("inputs", {}):
            fail(f"{label} binding {name}: missing input {node}.{input_name}")


def check_comfy_workflow() -> None:
    config = load_yaml("config/models.yaml")
    model = config["image_models"]["z-image-turbo"]

    t2i_path = ROOT / model["workflow"]
    try:
        t2i = json.loads(t2i_path.read_text(encoding="utf-8"))
    except Exception as exc:
        fail(f"ComfyUI T2I JSON: {exc}")

    validate_graph(
        t2i,
        model.get("bindings") or {},
        {
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
        },
        "Z-Image T2I",
    )
    if t2i["3"]["inputs"].get("steps") != 8:
        fail("Z-Image smoke workflow powinien domyślnie używać 8 kroków")

    control_path = ROOT / str(model.get("control_workflow"))
    try:
        control = json.loads(control_path.read_text(encoding="utf-8"))
    except Exception as exc:
        fail(f"ComfyUI ControlNet JSON: {exc}")

    validate_graph(
        control,
        model.get("control_bindings") or {},
        {
            "UNETLoader",
            "CLIPLoader",
            "VAELoader",
            "ModelPatchLoader",
            "LoadImage",
            "ImageScaleToTotalPixels",
            "Canny",
            "GetImageSize",
            "QwenImageDiffsynthControlnet",
            "CLIPTextEncode",
            "ConditioningZeroOut",
            "EmptySD3LatentImage",
            "ModelSamplingAuraFlow",
            "KSampler",
            "VAEDecode",
            "SaveImage",
        },
        "Z-Image ControlNet",
    )
    if control["7"]["inputs"].get("steps") != 9:
        fail("Z-Image ControlNet workflow powinien domyślnie używać 9 kroków")

    flux = config["image_models"]["flux2-klein-edit"]
    flux_path = ROOT / flux["workflow"]
    try:
        edit_graph = json.loads(flux_path.read_text(encoding="utf-8"))
    except Exception as exc:
        fail(f"ComfyUI FLUX.2 edit JSON: {exc}")

    validate_graph(
        edit_graph,
        flux.get("bindings") or {},
        {
            "UNETLoader",
            "CLIPLoader",
            "VAELoader",
            "LoadImage",
            "ImageScaleToTotalPixels",
            "GetImageSize",
            "CLIPTextEncode",
            "VAEEncode",
            "ReferenceLatent",
            "CFGGuider",
            "KSamplerSelect",
            "Flux2Scheduler",
            "RandomNoise",
            "EmptyFlux2LatentImage",
            "SamplerCustomAdvanced",
            "VAEDecode",
            "SaveImage",
        },
        "FLUX.2 Klein edit",
    )
    if edit_graph["14"]["inputs"].get("steps") != 20:
        fail("FLUX.2 edit workflow powinien domyślnie używać 20 kroków")

    print("[OK] ComfyUI Z-Image + ControlNet + FLUX.2 edit graphs + bindings")


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
    required_files = [
        "setup/ensure-comfyui.ps1",
        "scripts/preflight.py",
        "scripts/validate_short.py",
        "scripts/generate_images.py",
        "scripts/generate_tts.py",
        "scripts/transcribe.py",
        "scripts/sound_design.py",
        "scripts/render.py",
    ]
    for item in required_files:
        if item not in workflow:
            fail(f"Build workflow nie wywołuje {item}")
        if not (ROOT / item).exists():
            fail(f"Build workflow wskazuje brakujący plik {item}")
    print("[OK] Build workflow references")


def check_setup_files() -> None:
    required = [
        ROOT / "setup" / "windows-bootstrap.ps1",
        ROOT / "setup" / "install-zimage.ps1",
        ROOT / "setup" / "install-flux2-klein.ps1",
        ROOT / "setup" / "install-github-runner.ps1",
        ROOT / "setup" / "ensure-comfyui.ps1",
    ]
    for path in required:
        if not path.exists() or not path.read_text(encoding="utf-8").strip():
            fail(f"brak setup file: {path}")
    print(f"[OK] Setup files present: {len(required)}")


def main() -> None:
    check_python()
    check_yaml()
    check_comfy_workflow()
    check_short()
    check_workflow_references()
    check_setup_files()
    print("STATIC CHECKS OK")


if __name__ == "__main__":
    main()
