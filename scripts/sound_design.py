from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path

from common import load_yaml, short_output_dir


def ffprobe_duration(path: Path, ffprobe: str) -> float:
    result = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(result.stdout.strip())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("short_file")
    args = parser.parse_args()

    short = load_yaml(args.short_file)
    sound = short.get("sound") or {}
    render_cfg = load_yaml("config/models.yaml")["render"]
    ffmpeg = str(render_cfg.get("ffmpeg", "ffmpeg"))
    ffprobe = str(render_cfg.get("ffprobe", "ffprobe"))

    out_dir = short_output_dir(short)
    audio_dir = out_dir / "audio"
    voice = audio_dir / "voice.wav"
    mixed = audio_dir / "final_mix.wav"
    timings_path = audio_dir / "tts-timings.json"

    if not voice.exists():
        raise FileNotFoundError(voice)

    ambience = str(sound.get("ambience") or "").strip().lower()
    impact_enabled = bool(sound.get("impact_before_end", False))

    if not ambience and not impact_enabled:
        shutil.copy2(voice, mixed)
        print(f"SAVED {mixed} (voice only)")
        return

    duration = ffprobe_duration(voice, ffprobe)
    impact_time = max(0.0, duration - 1.0)
    if timings_path.exists():
        timings = json.loads(timings_path.read_text(encoding="utf-8"))
        scenes = timings.get("scenes") or []
        if len(scenes) == 8:
            impact_time = float(scenes[7]["start"])

    cmd: list[str] = [ffmpeg, "-y", "-i", str(voice)]
    filters: list[str] = [
        "[0:a]aresample=48000,aformat=channel_layouts=stereo,volume=1.0[voice]"
    ]
    mix_labels = ["[voice]"]
    input_index = 1

    # Procedural backgrounds are intentionally subtle and copyright-free.
    # They are a fallback; production sound assets can replace this module later.
    if ambience:
        if ambience in {"basement_roomtone", "interior_roomtone", "roomtone"}:
            noise_color = "pink"
            noise_volume = 0.055
            drone_freq = 47
            drone_volume = 0.035
        elif ambience in {"forest_night", "forest", "outdoor_night"}:
            noise_color = "brown"
            noise_volume = 0.045
            drone_freq = 39
            drone_volume = 0.02
        else:
            noise_color = "pink"
            noise_volume = 0.035
            drone_freq = 43
            drone_volume = 0.02

        cmd += [
            "-f",
            "lavfi",
            "-t",
            f"{duration:.4f}",
            "-i",
            f"anoisesrc=color={noise_color}:amplitude=0.08:sample_rate=48000",
        ]
        filters.append(
            f"[{input_index}:a]highpass=f=80,lowpass=f=1200,volume={noise_volume},"
            "aformat=channel_layouts=stereo[ambience]"
        )
        mix_labels.append("[ambience]")
        input_index += 1

        cmd += [
            "-f",
            "lavfi",
            "-t",
            f"{duration:.4f}",
            "-i",
            f"sine=frequency={drone_freq}:sample_rate=48000",
        ]
        filters.append(
            f"[{input_index}:a]lowpass=f=100,volume={drone_volume},"
            "aformat=channel_layouts=stereo[drone]"
        )
        mix_labels.append("[drone]")
        input_index += 1

    if impact_enabled:
        delay_ms = max(0, int(round(impact_time * 1000)))
        cmd += [
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=58:sample_rate=48000:duration=0.55",
        ]
        filters.append(
            f"[{input_index}:a]lowpass=f=180,volume=0.55,"
            "afade=t=out:st=0.08:d=0.45,"
            f"adelay={delay_ms}:all=1,aformat=channel_layouts=stereo[impact]"
        )
        mix_labels.append("[impact]")

    filters.append(
        "".join(mix_labels)
        + f"amix=inputs={len(mix_labels)}:normalize=0:duration=first,"
        "alimiter=limit=0.92[aout]"
    )

    cmd += [
        "-filter_complex",
        ";".join(filters),
        "-map",
        "[aout]",
        "-ar",
        "48000",
        "-ac",
        "2",
        "-c:a",
        "pcm_s16le",
        str(mixed),
    ]

    print("SOUND:", " ".join(cmd))
    subprocess.run(cmd, check=True)
    print(f"SAVED {mixed}")
    if impact_enabled:
        print(f"IMPACT @ {impact_time:.3f}s (start sceny 8)")


if __name__ == "__main__":
    main()
