from __future__ import annotations

import argparse
from faster_whisper import WhisperModel

from common import load_yaml, short_output_dir


def srt_ts(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("short_file")
    args = parser.parse_args()

    short = load_yaml(args.short_file)
    cfg = load_yaml("config/models.yaml")["whisper"]
    out_dir = short_output_dir(short)
    audio = out_dir / "audio" / "voice.wav"
    subtitles = out_dir / "subtitles.srt"

    if not audio.exists():
        raise FileNotFoundError(audio)

    model = WhisperModel(cfg.get("model", "small"), device="cuda", compute_type="int8_float16")
    segments, _ = model.transcribe(
        str(audio),
        language=cfg.get("language", "pl"),
        vad_filter=True,
        word_timestamps=True,
    )

    lines = []
    idx = 1
    for seg in segments:
        text = seg.text.strip()
        if not text:
            continue
        lines += [str(idx), f"{srt_ts(seg.start)} --> {srt_ts(seg.end)}", text, ""]
        idx += 1

    subtitles.write_text("\n".join(lines), encoding="utf-8")
    print(f"SAVED {subtitles}")


if __name__ == "__main__":
    main()
