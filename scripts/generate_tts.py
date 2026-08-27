from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from common import ROOT, load_yaml, short_output_dir


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("short_file")
    args = parser.parse_args()

    short = load_yaml(args.short_file)
    cfg = load_yaml("config/models.yaml")["tts"]
    out_dir = short_output_dir(short) / "audio"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "voice.wav"
    text_file = out_dir / "narration.txt"
    text_file.write_text(short["narration"], encoding="utf-8")

    engine = cfg.get("engine", "chatterbox")
    if engine != "chatterbox":
        raise RuntimeError(f"Nieobsługiwany silnik TTS: {engine}")

    # Adapter celowo korzysta z osobnego lokalnego CLI. Dzięki temu możemy
    # wymienić implementację TTS bez przebudowy pipeline'u.
    cli = ROOT / "tools" / "chatterbox_tts.py"
    if not cli.exists():
        raise FileNotFoundError(
            "Brak tools/chatterbox_tts.py. Uruchom setup i zainstaluj adapter Chatterbox."
        )

    cmd = [
        "python", str(cli),
        "--text-file", str(text_file),
        "--output", str(out),
        "--language", str(cfg.get("language", "pl")),
    ]
    ref = cfg.get("reference_audio")
    if ref:
        ref_path = ROOT / ref
        if ref_path.exists():
            cmd += ["--reference", str(ref_path)]

    print("TTS:", " ".join(cmd))
    subprocess.run(cmd, check=True)
    print(f"SAVED {out}")


if __name__ == "__main__":
    main()
