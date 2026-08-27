from __future__ import annotations

import argparse
import json
from pathlib import Path

import torch
import torchaudio
from chatterbox.mtl_tts import ChatterboxMultilingualTTS


def main() -> None:
    parser = argparse.ArgumentParser(description="CSP Chatterbox multilingual TTS adapter")
    parser.add_argument("--segments-json", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--timings", required=True)
    parser.add_argument("--language", default="pl")
    parser.add_argument("--reference")
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--model-version", default="v3")
    parser.add_argument("--exaggeration", type=float, default=0.55)
    parser.add_argument("--cfg-weight", type=float, default=0.35)
    parser.add_argument("--temperature", type=float, default=0.8)
    parser.add_argument("--repetition-penalty", type=float, default=1.2)
    parser.add_argument("--default-pause-ms", type=int, default=60)
    args = parser.parse_args()

    segments_path = Path(args.segments_json)
    payload = json.loads(segments_path.read_text(encoding="utf-8"))
    segments = payload.get("segments") or []
    if not segments:
        raise ValueError("Brak segmentów TTS")

    device = args.device
    if device == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("Chatterbox skonfigurowany na CUDA, ale CUDA nie jest dostępna")

    print(f"Loading Chatterbox Multilingual {args.model_version} on {device}...")
    model = ChatterboxMultilingualTTS.from_pretrained(
        device=device,
        t3_model=args.model_version,
    )

    reference = Path(args.reference) if args.reference else None
    if reference and reference.exists():
        # Obliczamy embedding referencji raz. Wszystkie sceny korzystają z tych
        # samych conditionals, co ogranicza dryf głosu między segmentami.
        model.prepare_conditionals(
            str(reference), exaggeration=float(args.exaggeration)
        )
        print(f"VOICE REFERENCE: {reference}")
    elif reference:
        print(f"WARN: reference audio nie istnieje: {reference}; używam głosu wbudowanego")

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    timings_path = Path(args.timings)
    timings_path.parent.mkdir(parents=True, exist_ok=True)
    segment_dir = output.parent / "segments"
    segment_dir.mkdir(parents=True, exist_ok=True)

    full_parts: list[torch.Tensor] = []
    timeline: list[dict] = []
    cursor_samples = 0

    for index, segment in enumerate(segments):
        scene_id = int(segment["id"])
        text = str(segment["text"]).strip()
        if not text:
            raise ValueError(f"Pusty tekst TTS w scenie {scene_id}")

        print(f"TTS scene {scene_id}/{len(segments)}")
        wav = model.generate(
            text,
            language_id=args.language,
            audio_prompt_path=None,
            exaggeration=float(args.exaggeration),
            cfg_weight=float(args.cfg_weight),
            temperature=float(args.temperature),
            repetition_penalty=float(args.repetition_penalty),
        ).detach().cpu().float()

        if wav.ndim == 1:
            wav = wav.unsqueeze(0)
        if wav.shape[0] != 1:
            wav = wav.mean(dim=0, keepdim=True)

        segment_path = segment_dir / f"scene-{scene_id:02d}.wav"
        torchaudio.save(str(segment_path), wav, model.sr)

        start = cursor_samples / model.sr
        speech_end_samples = cursor_samples + wav.shape[-1]
        speech_end = speech_end_samples / model.sr

        full_parts.append(wav)
        cursor_samples = speech_end_samples

        pause_ms = int(segment.get("pause_after_ms", args.default_pause_ms))
        if index == len(segments) - 1:
            pause_ms = 0
        pause_samples = int(model.sr * max(0, pause_ms) / 1000)
        if pause_samples > 0:
            full_parts.append(torch.zeros((1, pause_samples), dtype=torch.float32))
            cursor_samples += pause_samples

        end = cursor_samples / model.sr
        timeline.append(
            {
                "id": scene_id,
                "text": text,
                "start": round(start, 4),
                "speech_end": round(speech_end, 4),
                "end": round(end, 4),
                "duration": round(end - start, 4),
                "speech_duration": round(speech_end - start, 4),
                "pause_after_ms": pause_ms,
            }
        )

    full_wav = torch.cat(full_parts, dim=-1)
    torchaudio.save(str(output), full_wav, model.sr)

    timings = {
        "sample_rate": model.sr,
        "duration": round(full_wav.shape[-1] / model.sr, 4),
        "default_pause_ms_between_scenes": int(args.default_pause_ms),
        "scenes": timeline,
    }
    timings_path.write_text(
        json.dumps(timings, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"SAVED {output}")
    print(f"SAVED {timings_path}")


if __name__ == "__main__":
    main()
