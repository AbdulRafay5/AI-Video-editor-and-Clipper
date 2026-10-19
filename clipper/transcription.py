import json
import logging
import os
import subprocess
import sys
import threading
import time

import config
from . import video

log = logging.getLogger("clipper.transcription")


def extract_audio(src, wav_path, cancel):
    """Pull a 16 kHz mono WAV out of the video (about 115 MB per hour)."""
    cmd = [video._tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
           "-i", str(src), "-vn", "-map", "0:a:0", "-ac", "1", "-ar", "16000",
           "-c:a", "pcm_s16le", str(wav_path)]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", errors="replace",
                            creationflags=video.NO_WINDOW)
    while True:
        try:
            _, err = proc.communicate(timeout=0.5)
            break
        except subprocess.TimeoutExpired:
            if cancel.is_set():
                proc.kill()
                proc.communicate()
                wav_path.unlink(missing_ok=True)
                raise video.Cancelled()
    if proc.returncode != 0:
        wav_path.unlink(missing_ok=True)
        raise video.VideoError("Could not extract audio: " + (err or "").strip()[-300:])


def _device_plan():
    d = config.WHISPER_DEVICE
    if d == "cuda":
        return [("cuda", config.WHISPER_GPU_COMPUTE)]
    if d == "cpu":
        return [("cpu", config.WHISPER_CPU_COMPUTE)]
    return [("cuda", config.WHISPER_GPU_COMPUTE), ("cpu", config.WHISPER_CPU_COMPUTE)]


def _run_worker(audio, out_json, device, compute, on_progress, cancel):
    cmd = [sys.executable, "-m", "clipper.transcribe_worker",
           "--audio", str(audio), "--out", str(out_json),
           "--model", config.WHISPER_MODEL, "--device", device, "--compute", compute,
           "--model-dir", str(config.MODEL_DIR),
           "--language", config.WHISPER_LANGUAGE or ""]
    env = {**os.environ, "HF_HUB_DISABLE_SYMLINKS_WARNING": "1", "PYTHONIOENCODING": "utf-8"}
    err_path = config.DATA_DIR / "transcribe_stderr.log"
    log.info("Transcription worker: device=%s compute=%s model=%s", device, compute, config.WHISPER_MODEL)

    with open(err_path, "w", encoding="utf-8") as errf:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=errf, text=True,
                                encoding="utf-8", errors="replace", cwd=config.BASE_DIR,
                                env=env, creationflags=video.NO_WINDOW)

        def watch():  # lets cancel work even while the model is downloading/loading
            while proc.poll() is None:
                if cancel.wait(0.5):
                    proc.kill()
                    return

        threading.Thread(target=watch, daemon=True).start()
        try:
            for line in proc.stdout:
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if msg.get("type") == "progress" and msg.get("duration"):
                    on_progress(min(msg["t"] / msg["duration"], 1.0))
        finally:
            proc.wait()

    if cancel.is_set():
        raise video.Cancelled()
    if proc.returncode != 0 or not out_json.exists():
        try:
            tail = err_path.read_text(encoding="utf-8", errors="replace").strip()[-500:]
        except OSError:
            tail = ""
        raise video.VideoError(f"Transcription on {device} failed (exit {proc.returncode}). {tail}")


def transcribe_project(project, cancel, on_progress, on_device):
    """Returns (result_dict, device_used). Tries GPU first if configured, then CPU."""
    pid = project["id"]
    if not video.probe(project["source_path"])["has_audio"]:
        raise video.VideoError("This video has no audio track, so there is nothing to transcribe.")

    wav = config.AUDIO_DIR / f"project_{pid}.wav"
    out = config.AUDIO_DIR / f"project_{pid}.json"
    try:
        extract_audio(project["source_path"], wav, cancel)
        last_error = None
        for device, compute in _device_plan():
            on_device(device)
            out.unlink(missing_ok=True)
            try:
                _run_worker(wav, out, device, compute, on_progress, cancel)
                return json.loads(out.read_text(encoding="utf-8")), device
            except video.Cancelled:
                raise
            except video.VideoError as e:
                log.warning("%s", e)
                last_error = e
        raise last_error
    finally:
        wav.unlink(missing_ok=True)
        out.unlink(missing_ok=True)