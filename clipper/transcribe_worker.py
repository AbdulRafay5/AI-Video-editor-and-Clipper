"""Runs faster-whisper in a separate process so a GPU/library crash can't take
down the web app, and GPU memory is fully released when it exits."""
import argparse
import json
import os
import site
import sysconfig
from pathlib import Path

# --- Tuning knobs for missed or invented lines ---
USE_VAD = False            # False = transcribe everything (test setting). True = skip non-speech.
VAD_THRESHOLD = 0.3        # lower = more sensitive (default is 0.5). Only used if USE_VAD is True.
VAD_MIN_SILENCE_MS = 500   # silence length that splits speech. Only used if USE_VAD is True.
VAD_SPEECH_PAD_MS = 400    # padding kept around detected speech. Only used if USE_VAD is True.


def add_nvidia_dll_dirs():
    """Make the pip-installed CUDA libraries (nvidia-cublas-cu12 etc.) findable on Windows."""
    if os.name != "nt":
        return
    bases = {sysconfig.get_paths()["purelib"], *site.getsitepackages()}
    for base in bases:
        nv = Path(base) / "nvidia"
        if not nv.is_dir():
            continue
        for bin_dir in nv.glob("*/bin"):
            os.add_dll_directory(str(bin_dir))
            os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")


def emit(**msg):
    print(json.dumps(msg), flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--audio", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--device", required=True)
    ap.add_argument("--compute", required=True)
    ap.add_argument("--model-dir", required=True)
    ap.add_argument("--language", default="")
    args = ap.parse_args()

    add_nvidia_dll_dirs()
    from faster_whisper import WhisperModel

    emit(type="status", message="Loading model (first run downloads it)")
    model = WhisperModel(args.model, device=args.device, compute_type=args.compute,
                         download_root=args.model_dir)

    emit(type="status", message="Transcribing")
    kwargs = dict(
        language=args.language or None,
        word_timestamps=True,
        beam_size=5,
        condition_on_previous_text=False,
        vad_filter=USE_VAD,
    )
    if USE_VAD:
        kwargs["vad_parameters"] = dict(
            threshold=VAD_THRESHOLD,
            min_silence_duration_ms=VAD_MIN_SILENCE_MS,
            speech_pad_ms=VAD_SPEECH_PAD_MS,
        )
    segments, info = model.transcribe(args.audio, **kwargs)

    out_segments = []
    for seg in segments:
        out_segments.append({
            "start": round(seg.start, 3),
            "end": round(seg.end, 3),
            "text": seg.text.strip(),
            "words": [
                {"start": round(w.start, 3), "end": round(w.end, 3),
                 "word": w.word.strip(), "prob": round(w.probability, 3)}
                for w in (seg.words or [])
            ],
        })
        emit(type="progress", t=seg.end, duration=info.duration)

    result = {
        "language": info.language,
        "language_probability": round(info.language_probability, 3),
        "duration": info.duration,
        "segments": out_segments,
    }
    Path(args.out).write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    emit(type="done", segments=len(out_segments))


if __name__ == "__main__":
    main()