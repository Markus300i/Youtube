from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import requests

from common import OUTPUT_ROOT, ROOT, load_yaml


class Preflight:
    def __init__(self) -> None:
        self.failures: list[str] = []
        self.warnings: list[str] = []

    def ok(self, message: str) -> None:
        print(f"[OK]   {message}")

    def fail(self, message: str) -> None:
        self.failures.append(message)
        print(f"[FAIL] {message}")

    def warn(self, message: str) -> None:
        self.warnings.append(message)
        print(f"[WARN] {message}")


def run_text(command: list[str]) -> tuple[int, str]:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        return result.returncode, (result.stdout or "") + (result.stderr or "")
    except Exception as exc:
        return 1, str(exc)


def find_recursive(value: Any, needle: str) -> bool:
    if isinstance(value, str):
        return needle in value
    if isinstance(value, dict):
        return any(find_recursive(item, needle) for item in value.values())
    if isinstance(value, list):
        return any(find_recursive(item, needle) for item in value)
    return False


def validate_workflow_bindings(
    check: Preflight,
    workflow_path: Path,
    bindings: dict[str, Any],
    label: str,
) -> None:
    try:
        workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
        for binding_name, binding in bindings.items():
            node_id = str(binding["node"])
            input_name = str(binding["input"])
            if node_id not in workflow:
                check.fail(
                    f"{label}: binding {binding_name} wskazuje brak node {node_id}"
                )
            elif input_name not in workflow[node_id].get("inputs", {}):
                check.fail(
                    f"{label}: binding {binding_name} wskazuje brak input "
                    f"{node_id}.{input_name}"
                )
        check.ok(f"Workflow JSON odczytany: {workflow_path.name}")
    except Exception as exc:
        check.fail(f"{label} workflow ComfyUI jest nieprawidłowy: {exc}")


def main() -> int:
    check = Preflight()
    cfg = load_yaml("config/models.yaml")
    image_cfg = cfg["image_models"]["z-image-turbo"]
    comfy_url = os.getenv("CSP_COMFY_URL", cfg["comfyui"]["base_url"]).rstrip("/")

    print("== CSP AUTOMATION PREFLIGHT ==")
    print(f"Repo:       {ROOT}")
    print(f"Output:     {OUTPUT_ROOT}")
    print(f"Python:     {sys.executable}")
    print(f"ComfyUI:    {comfy_url}")
    print()

    # NVIDIA / CUDA
    if shutil.which("nvidia-smi"):
        code, text = run_text(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,driver_version",
                "--format=csv,noheader",
            ]
        )
        if code == 0:
            check.ok("NVIDIA: " + text.strip().replace("\n", " | "))
        else:
            check.fail("nvidia-smi zwrócił błąd")
    else:
        check.fail("nvidia-smi nie jest dostępne w PATH")

    try:
        import torch

        if torch.cuda.is_available():
            props = torch.cuda.get_device_properties(0)
            check.ok(
                f"PyTorch CUDA: {torch.__version__}, {props.name}, "
                f"{props.total_memory / (1024**3):.1f} GB VRAM"
            )
        else:
            check.fail("PyTorch działa, ale torch.cuda.is_available() == False")
    except Exception as exc:
        check.fail(f"Nie można zaimportować/uruchomić PyTorch CUDA: {exc}")

    # Required Python modules
    for module in ("yaml", "requests", "faster_whisper", "PIL"):
        try:
            __import__(module)
            check.ok(f"Python module: {module}")
        except Exception as exc:
            check.fail(f"Brak modułu {module}: {exc}")

    try:
        from chatterbox.mtl_tts import ChatterboxMultilingualTTS  # noqa: F401

        check.ok("Chatterbox Multilingual jest dostępny")
    except Exception as exc:
        check.fail(f"Chatterbox Multilingual nie ładuje się: {exc}")

    # FFmpeg
    ffmpeg = str(cfg["render"].get("ffmpeg", "ffmpeg"))
    ffprobe = str(cfg["render"].get("ffprobe", "ffprobe"))
    if shutil.which(ffmpeg):
        code, encoders = run_text([ffmpeg, "-hide_banner", "-encoders"])
        if code == 0 and "h264_nvenc" in encoders:
            check.ok("FFmpeg: h264_nvenc dostępny")
        else:
            check.fail("FFmpeg nie ma enkodera h264_nvenc")

        code, filters = run_text([ffmpeg, "-hide_banner", "-filters"])
        if code == 0 and (" ass " in filters or " ass" in filters):
            check.ok("FFmpeg: filtr ASS/libass dostępny")
        else:
            check.fail("FFmpeg nie ma filtra ass/libass — napisy nie zostaną wypalone")
    else:
        check.fail(f"Nie znaleziono FFmpeg: {ffmpeg}")

    if shutil.which(ffprobe):
        check.ok("ffprobe dostępny")
    else:
        check.fail(f"Nie znaleziono ffprobe: {ffprobe}")

    # Persistent directories / voice identity
    try:
        OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
        probe = OUTPUT_ROOT / ".csp-write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        check.ok(f"Katalog output jest zapisywalny: {OUTPUT_ROOT}")
    except Exception as exc:
        check.fail(f"Nie można zapisywać do CSP_OUTPUT_DIR: {exc}")

    reference_raw = os.getenv("CSP_VOICE_REFERENCE") or cfg["tts"].get("reference_audio")
    if reference_raw:
        reference = Path(reference_raw)
        if not reference.is_absolute():
            reference = ROOT / reference
        if reference.exists() and reference.stat().st_size > 1000:
            check.ok(f"Referencja głosu: {reference}")
        else:
            check.warn(
                f"Brak referencji narratora: {reference}. TTS zadziała, ale własny stały głos nie będzie zagwarantowany."
            )

    # ComfyUI API
    try:
        response = requests.get(f"{comfy_url}/system_stats", timeout=10)
        response.raise_for_status()
        stats = response.json()
        devices = stats.get("devices") or []
        if devices:
            check.ok(
                "ComfyUI działa; device: "
                + ", ".join(str(item.get("name", "GPU")) for item in devices)
            )
        else:
            check.ok("ComfyUI /system_stats odpowiada")
    except Exception as exc:
        check.fail(f"ComfyUI nie odpowiada pod {comfy_url}: {exc}")
        return finish(check)

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
    if image_cfg.get("control_workflow"):
        required_classes.update(
            {
                "ModelPatchLoader",
                "QwenImageDiffsynthControlnet",
                "LoadImage",
                "ImageScaleToTotalPixels",
                "Canny",
                "GetImageSize",
            }
        )

    try:
        response = requests.get(f"{comfy_url}/object_info", timeout=30)
        response.raise_for_status()
        object_info = response.json()
        missing_classes = sorted(required_classes - set(object_info))
        if missing_classes:
            check.fail("ComfyUI nie ma wymaganych node'ów: " + ", ".join(missing_classes))
        else:
            check.ok("Wszystkie wymagane core nodes ComfyUI są dostępne")

        models = image_cfg.get("models") or {}
        for kind, filename in models.items():
            if not filename:
                continue
            if find_recursive(object_info, str(filename)):
                check.ok(f"ComfyUI widzi model {kind}: {filename}")
            else:
                check.fail(
                    f"ComfyUI nie widzi modelu {kind}: {filename}. "
                    "Uruchom setup/install-zimage.ps1 i zrestartuj ComfyUI."
                )
    except Exception as exc:
        check.fail(f"Nie udało się odczytać /object_info ComfyUI: {exc}")

    workflow_path = ROOT / image_cfg["workflow"]
    validate_workflow_bindings(
        check,
        workflow_path,
        image_cfg.get("bindings") or {},
        "Z-Image T2I",
    )

    control_workflow = image_cfg.get("control_workflow")
    if control_workflow:
        validate_workflow_bindings(
            check,
            ROOT / str(control_workflow),
            image_cfg.get("control_bindings") or {},
            "Z-Image ControlNet",
        )

    return finish(check)


def finish(check: Preflight) -> int:
    print()
    print(f"Failures: {len(check.failures)} | Warnings: {len(check.warnings)}")
    if check.failures:
        print("PREFLIGHT FAILED")
        return 1
    print("PREFLIGHT OK — komputer jest gotowy do pipeline'u CSP")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
