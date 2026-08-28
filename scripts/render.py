from __future__ import annotations

import argparse
import json
import math
import subprocess
from pathlib import Path

from common import load_yaml, short_output_dir


def ffprobe_duration(path: Path, ffprobe: str = "ffprobe") -> float:
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


def run(cmd: list[str]) -> None:
    print("RUN:", " ".join(cmd))
    subprocess.run(cmd, check=True)


def motion_filter(motion: str, duration: float, fps: int) -> str:
    frames = max(2, int(math.ceil(duration * fps)))
    base = (
        "scale=1200:2134:force_original_aspect_ratio=increase,"
        "crop=1200:2134"
    )

    motion = (motion or "static").lower()
    if motion == "static":
        return (
            base
            + ",scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920,fps=%d,format=yuv420p" % fps
        )

    if motion in {"push_in", "slow_push"}:
        amount = 0.08 if motion == "push_in" else 0.05
        increment = amount / max(1, frames - 1)
        return (
            base
            + f",zoompan=z='min(1+on*{increment:.9f},{1 + amount:.4f})'"
            + ":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
            + f":d=1:s=1080x1920:fps={fps},format=yuv420p"
        )

    if motion in {"pan_left", "pan_right"}:
        denom = max(1, frames - 1)
        if motion == "pan_right":
            x_expr = f"(iw-iw/zoom)*min(on/{denom},1)"
        else:
            x_expr = f"(iw-iw/zoom)*(1-min(on/{denom},1))"
        return (
            base
            + ",zoompan=z='1.08'"
            + f":x='{x_expr}':y='ih/2-(ih/zoom/2)'"
            + f":d=1:s=1080x1920:fps={fps},format=yuv420p"
        )

    print(f"WARN: nieznany motion={motion}; używam static")
    return (
        base
        + ",scale=1080:1920:force_original_aspect_ratio=increase,"
        "crop=1080:1920,fps=%d,format=yuv420p" % fps
    )


def load_scene_durations(out_dir: Path, audio_duration: float) -> list[float]:
    timings = out_dir / "audio" / "tts-timings.json"
    if timings.exists():
        data = json.loads(timings.read_text(encoding="utf-8"))
        scenes = data.get("scenes") or []
        if len(scenes) == 8:
            durations = [max(0.25, float(item["duration"])) for item in scenes]
            print("TIMING: exact scene durations from Chatterbox")
            return durations
        print("WARN: tts-timings.json nie zawiera 8 scen; fallback równy")

    print("WARN: brak dokładnych timingów TTS; fallback równy")
    return [audio_duration / 8.0] * 8


def escape_filter_path(path: Path) -> str:
    value = path.resolve().as_posix()
    value = value.replace("\\", "/").replace(":", "\\:")
    value = value.replace("'", "\\'")
    return value


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("short_file")
    args = parser.parse_args()

    short = load_yaml(args.short_file)
    cfg = load_yaml("config/models.yaml")["render"]
    out_cfg = short.get("output") or {}

    ffmpeg = cfg.get("ffmpeg", "ffmpeg")
    ffprobe = cfg.get("ffprobe", "ffprobe")
    codec = cfg.get("video_codec", "h264_nvenc")
    preset = cfg.get("preset", "p5")
    video_bitrate = cfg.get("video_bitrate", "8M")
    fps = int(out_cfg.get("fps", 30))

    out_dir = short_output_dir(short)
    images_dir = out_dir / "images"
    voice_audio = out_dir / "audio" / "voice.wav"
    mixed_audio = out_dir / "audio" / "final_mix.wav"
    audio = mixed_audio if mixed_audio.exists() else voice_audio
    subtitles_ass = out_dir / "subtitles.ass"
    subtitles_srt = out_dir / "subtitles.srt"
    final = out_dir / "final.mp4"
    temp_dir = out_dir / "render-temp"
    temp_dir.mkdir(parents=True, exist_ok=True)

    for i in range(1, 9):
        image = images_dir / f"scene-{i:02d}.png"
        if not image.exists():
            raise FileNotFoundError(image)
    if not voice_audio.exists():
        raise FileNotFoundError(voice_audio)
    if audio == mixed_audio:
        print(f"AUDIO: używam sound designu {mixed_audio}")
    else:
        print("AUDIO: final_mix.wav nie istnieje — używam samego voice.wav")

    # Timingi obrazu liczymy z czystej narracji / pliku TTS, nie z ewentualnego
    # miksu ambience, aby zmiany scen były zawsze zsynchronizowane z lektorem.
    voice_duration = ffprobe_duration(voice_audio, ffprobe=ffprobe)
    durations = load_scene_durations(out_dir, voice_duration)

    clips: list[Path] = []
    for i, scene in enumerate(short["scenes"], start=1):
        duration = durations[i - 1]
        image = images_dir / f"scene-{i:02d}.png"
        clip = temp_dir / f"scene-{i:02d}.mp4"
        clips.append(clip)
        vf = motion_filter(str(scene.get("motion", "static")), duration, fps)

        cmd = [
            ffmpeg,
            "-y",
            "-loop",
            "1",
            "-framerate",
            str(fps),
            "-i",
            str(image),
            "-t",
            f"{duration:.4f}",
            "-vf",
            vf,
            "-an",
            "-c:v",
            codec,
            "-preset",
            preset,
            "-b:v",
            "12M",
            "-pix_fmt",
            "yuv420p",
            str(clip),
        ]
        run(cmd)

    concat_file = temp_dir / "clips.txt"
    concat_file.write_text(
        "\n".join(f"file '{clip.resolve().as_posix()}'" for clip in clips),
        encoding="utf-8",
    )
    silent = temp_dir / "silent.mp4"
    run(
        [
            ffmpeg,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_file),
            "-c",
            "copy",
            str(silent),
        ]
    )

    final_cmd = [ffmpeg, "-y", "-i", str(silent), "-i", str(audio)]
    if subtitles_ass.exists():
        final_cmd += ["-vf", f"ass='{escape_filter_path(subtitles_ass)}'"]
    elif subtitles_srt.exists():
        final_cmd += ["-vf", f"subtitles='{escape_filter_path(subtitles_srt)}'"]

    final_cmd += [
        "-c:v",
        codec,
        "-preset",
        preset,
        "-b:v",
        str(video_bitrate),
        "-c:a",
        cfg.get("audio_codec", "aac"),
        "-b:a",
        cfg.get("audio_bitrate", "192k"),
        "-shortest",
        "-movflags",
        "+faststart",
        str(final),
    ]
    run(final_cmd)
    print(f"SAVED {final}")


if __name__ == "__main__":
    main()
