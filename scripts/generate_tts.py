from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from common import ROOT, load_yaml, short_output_dir


def resolve_reference(cfg: dict) -> Path | None:
    raw = os.getenv("CSP_VOICE_REFERENCE") or cfg.get("reference_audio")
    if not raw:
        return None
    path = Path(str(raw)).expanduser()
    if not path.is_absolute():
        path = ROOT / path
    return path.resolve()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("short_file")
    args = parser.parse_args()

    short = load_yaml(args.short_file)
    cfg = load_yaml("config/models.yaml")["tts"]
    out_dir = short_output_dir(short) / "audio"
    out_dir.mkdir(parents=True, exist_ok=True)

    output = out_dir / "voice.wav"
    timings = out_dir / "tts-timings.json"
    segments_json = out_dir / "tts-segments.json"

    segments = [
        {"id": int(scene["id"]), "text": str(scene["text"]).strip()}
        for scene in short["scenes"]
    ]
    segments_json.write_text(
        json.dumps({"segments": segments}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    engine = cfg.get("engine", "chatterbox")
    if engine != "chatterbox":
        raise RuntimeError(f"Nieobsługiwany silnik TTS: {engine}")

    cli = ROOT / "tools" / "chatterbox_tts.py"
    if not cli.exists():
        raise FileNotFoundError(cli)

    cmd = [
        sys.executable,
        str(cli),
        "--segments-json",
        str(segments_json),
        "--output",
        str(output),
        "--timings",
        str(timings),
        "--language",
        str(cfg.get("language", "pl")),
        "--device",
        str(cfg.get("device", "cuda")),
        "--model-version",
        str(cfg.get("model_version", "v3")),
        "--exaggeration",
        str(cfg.get("exaggeration", 0.55)),
        "--cfg-weight",
        str(cfg.get("cfg_weight", 0.35)),
        "--temperature",
        str(cfg.get("temperature", 0.8)),
        "--repetition-penalty",
        str(cfg.get("repetition_penalty", 1.2)),
        "--pause-ms",
        str(cfg.get("pause_ms_between_scenes", 60)),
    ]

    reference_path = resolve_reference(cfg)
    if reference_path:
        if reference_path.exists():
            cmd += ["--reference", str(reference_path)]
        else:
            print(
                f"WARN: brak {reference_path}; Chatterbox użyje głosu wbudowanego. "
                "Dodaj referencję narratora, aby utrzymać własną tożsamość głosu."
            )

    print("TTS:", " ".join(cmd))
    subprocess.run(cmd, check=True)


if __name__ == "__main__":
    main()
