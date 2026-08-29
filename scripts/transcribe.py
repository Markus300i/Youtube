from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from faster_whisper import WhisperModel

from common import load_yaml, short_output_dir


PLAY_RES_X = 1080
PLAY_RES_Y = 1920
DEFAULT_MAX_CHARS = 32
DEFAULT_MAX_LINES = 2
DEFAULT_SIDE_MARGIN = 96  # 8.9% of 1080
DEFAULT_BOTTOM_MARGIN = 190  # 9.9% of 1920
DEFAULT_FONT_SIZE = 60


def srt_ts(seconds: float) -> str:
    ms = max(0, int(round(seconds * 1000)))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def ass_ts(seconds: float) -> str:
    cs = max(0, int(round(seconds * 100)))
    h, rem = divmod(cs, 360_000)
    m, rem = divmod(rem, 6_000)
    s, cs = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def clean_word(raw: str) -> str:
    return re.sub(r"\s+", " ", raw).strip()


def chunk_words(words: list[dict], max_words: int) -> list[dict]:
    if not words:
        return []

    chunks: list[list[dict]] = []
    current: list[dict] = []

    for word in words:
        if current:
            gap = float(word["start"]) - float(current[-1]["end"])
            duration = float(current[-1]["end"]) - float(current[0]["start"])
            if gap > 0.32 or (duration > 1.55 and len(current) >= 2):
                chunks.append(current)
                current = []

        current.append(word)
        token = str(word["word"])
        punctuation_break = bool(re.search(r"[.!?…,:;]$", token))
        if len(current) >= max_words or (punctuation_break and len(current) >= 2):
            chunks.append(current)
            current = []

    if current:
        chunks.append(current)

    # Avoid single-word flashes when they can be merged without exceeding max_words.
    merged: list[list[dict]] = []
    for chunk in chunks:
        if len(chunk) == 1 and merged and len(merged[-1]) < max_words:
            merged[-1].extend(chunk)
        else:
            merged.append(chunk)

    return [
        {
            "start": float(chunk[0]["start"]),
            "end": float(chunk[-1]["end"]),
            "text": " ".join(str(w["word"]).strip() for w in chunk).strip(),
        }
        for chunk in merged
    ]


def wrap_subtitle_text(
    text: str,
    max_chars: int = DEFAULT_MAX_CHARS,
    max_lines: int = DEFAULT_MAX_LINES,
) -> list[str]:
    """Wrap a subtitle into at most two balanced lines for a 9:16 frame."""
    words = clean_word(text).split()
    if not words:
        return []
    if max_lines <= 1:
        return [" ".join(words)]

    full = " ".join(words)
    if len(full) <= max_chars:
        return [full]

    best: tuple[float, list[str]] | None = None
    for split in range(1, len(words)):
        left = " ".join(words[:split])
        right = " ".join(words[split:])
        longest = max(len(left), len(right))
        if longest > max_chars:
            continue
        score = abs(len(left) - len(right)) + longest * 0.05
        if best is None or score < best[0]:
            best = (score, [left, right])

    if best is not None:
        return best[1]

    # A chunk should normally be short enough already. If it is not, keep two
    # lines and split at the most balanced word boundary instead of letting ASS
    # auto-wrap into three or more lines.
    split = min(
        range(1, len(words)),
        key=lambda idx: abs(
            len(" ".join(words[:idx])) - len(" ".join(words[idx:]))
        ),
    )
    return [" ".join(words[:split]), " ".join(words[split:])]


def subtitle_text(text: str, uppercase: bool, separator: str, max_chars: int) -> str:
    if uppercase:
        text = text.upper()
    return separator.join(wrap_subtitle_text(text, max_chars=max_chars, max_lines=2))


def write_srt(
    path: Path,
    chunks: list[dict],
    uppercase: bool,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> None:
    lines: list[str] = []
    for idx, chunk in enumerate(chunks, start=1):
        text = subtitle_text(str(chunk["text"]), uppercase, "\n", max_chars)
        lines.extend(
            [
                str(idx),
                f"{srt_ts(chunk['start'])} --> {srt_ts(chunk['end'])}",
                text,
                "",
            ]
        )
    path.write_text("\n".join(lines), encoding="utf-8")


def write_ass(
    path: Path,
    chunks: list[dict],
    uppercase: bool,
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
    font_size: int = DEFAULT_FONT_SIZE,
    side_margin: int = DEFAULT_SIDE_MARGIN,
    bottom_margin: int = DEFAULT_BOTTOM_MARGIN,
) -> None:
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {PLAY_RES_X}
PlayResY: {PLAY_RES_Y}
ScaledBorderAndShadow: yes
WrapStyle: 2

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: CSP,Arial,{font_size},&H00F4F4F4,&H00F4F4F4,&H00101010,&H70000000,-1,0,0,0,100,100,0,0,1,4,2,2,{side_margin},{side_margin},{bottom_margin},1

[Events]
Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
"""
    events: list[str] = []
    for chunk in chunks:
        text = subtitle_text(str(chunk["text"]), uppercase, r"\N", max_chars)
        text = text.replace("{", "\\{").replace("}", "\\}")
        events.append(
            f"Dialogue: 0,{ass_ts(chunk['start'])},{ass_ts(chunk['end'])},CSP,,0,0,0,,{text}"
        )
    path.write_text(header + "\n".join(events) + "\n", encoding="utf-8-sig")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("short_file")
    args = parser.parse_args()

    short = load_yaml(args.short_file)
    cfg = load_yaml("config/models.yaml")["whisper"]
    subtitle_cfg = short.get("subtitles") or {}
    out_dir = short_output_dir(short)
    audio = out_dir / "audio" / "voice.wav"
    srt_path = out_dir / "subtitles.srt"
    ass_path = out_dir / "subtitles.ass"
    words_path = out_dir / "transcription-words.json"

    if not audio.exists():
        raise FileNotFoundError(audio)

    model = WhisperModel(
        cfg.get("model", "small"),
        device=cfg.get("device", "cuda"),
        compute_type=cfg.get("compute_type", "int8_float16"),
    )
    segments, _ = model.transcribe(
        str(audio),
        language=cfg.get("language", "pl"),
        vad_filter=True,
        word_timestamps=True,
        beam_size=5,
    )

    words: list[dict] = []
    for segment in segments:
        for word in segment.words or []:
            token = clean_word(word.word)
            if not token or word.start is None or word.end is None:
                continue
            words.append(
                {
                    "word": token,
                    "start": round(float(word.start), 4),
                    "end": round(float(word.end), 4),
                    "probability": round(float(word.probability or 0.0), 4),
                }
            )

    if not words:
        raise RuntimeError("Whisper nie zwrócił timestampów słów")

    max_words = max(2, min(5, int(subtitle_cfg.get("max_words", 4))))
    max_chars = max(24, min(36, int(subtitle_cfg.get("max_chars_per_line", 32))))
    uppercase = bool(subtitle_cfg.get("uppercase", True))
    font_size = max(48, min(68, int(subtitle_cfg.get("font_size", DEFAULT_FONT_SIZE))))
    side_margin = max(86, min(120, int(subtitle_cfg.get("side_margin", DEFAULT_SIDE_MARGIN))))
    bottom_margin = max(155, min(230, int(subtitle_cfg.get("bottom_margin", DEFAULT_BOTTOM_MARGIN))))
    chunks = chunk_words(words, max_words=max_words)

    write_srt(srt_path, chunks, uppercase, max_chars=max_chars)
    write_ass(
        ass_path,
        chunks,
        uppercase,
        max_chars=max_chars,
        font_size=font_size,
        side_margin=side_margin,
        bottom_margin=bottom_margin,
    )
    words_path.write_text(
        json.dumps({"words": words, "chunks": chunks}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"SAVED {srt_path}")
    print(f"SAVED {ass_path}")
    print(f"SAVED {words_path}")


if __name__ == "__main__":
    main()
