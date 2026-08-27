from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from common import load_yaml, short_output_dir


def ffprobe_duration(path: Path) -> float:
    result = subprocess.run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path)
    ], capture_output=True, text=True, check=True)
    return float(result.stdout.strip())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("short_file")
    args = parser.parse_args()

    short = load_yaml(args.short_file)
    cfg = load_yaml("config/models.yaml")["render"]
    out_dir = short_output_dir(short)
    images_dir = out_dir / "images"
    audio = out_dir / "audio" / "voice.wav"
    subtitles = out_dir / "subtitles.srt"
    final = out_dir / "final.mp4"

    for i in range(1, 9):
        p = images_dir / f"scene-{i:02d}.png"
        if not p.exists():
            raise FileNotFoundError(p)
    if not audio.exists():
        raise FileNotFoundError(audio)

    duration = ffprobe_duration(audio)
    scene_duration = duration / 8.0

    concat = out_dir / "images.txt"
    lines = []
    for i in range(1, 9):
        lines.append(f"file '{(images_dir / f'scene-{i:02d}.png').as_posix()}'")
        lines.append(f"duration {scene_duration:.3f}")
    lines.append(f"file '{(images_dir / 'scene-08.png').as_posix()}'")
    concat.write_text("\n".join(lines), encoding="utf-8")

    vf = (
        "scale=1080:1920:force_original_aspect_ratio=increase,"
        "crop=1080:1920,"
        "fps=30"
    )
    if subtitles.exists():
        escaped = subtitles.as_posix().replace(':', '\\:')
        vf += f",subtitles='{escaped}'"

    cmd = [
        cfg.get("ffmpeg", "ffmpeg"), "-y",
        "-f", "concat", "-safe", "0", "-i", str(concat),
        "-i", str(audio),
        "-vf", vf,
        "-c:v", cfg.get("video_codec", "h264_nvenc"),
        "-preset", "p5",
        "-b:v", "8M",
        "-c:a", cfg.get("audio_codec", "aac"),
        "-b:a", cfg.get("audio_bitrate", "192k"),
        "-shortest",
        str(final),
    ]
    print("RENDER:", " ".join(cmd))
    subprocess.run(cmd, check=True)
    print(f"SAVED {final}")


if __name__ == "__main__":
    main()
