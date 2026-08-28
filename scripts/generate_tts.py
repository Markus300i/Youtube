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
    sound_cfg = short.get("sound") or {}
    out_dir = short_output_dir(short) / "audio"
    out_dir.mkdir(parents=True, exist_ok=True)

    output = out_dir / "voice.wav"
    timings = out_dir / "tts-timings.json"
    segments_json = out_dir / "tts-segments.json"

    default_pause = int(cfg.get("pause_ms_between_scenes", 60))
    twist_pause = int(sound_cfg.get("silence_before_twist_ms", 0))

    segments = []
    for index, scene in enumerate(short["scenes"]):
        pause_after = default_pause if index < len(short["scenes"]) - 1 else 0
        # Scena 8 jest twistem. Pauza po scenie 7 zastępuje zwykły odstęp.
        if index == 6 and twist_pause > 0:
            pause_after = twist_pause
        segments.append(
            {
                "id": int(scene["id"]),
                "text": str(scene["text"]).strip(),
                "pause_after_ms": pause_after,
            }
        )

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
        "--default-pause-ms",
        str(default_pause),
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
