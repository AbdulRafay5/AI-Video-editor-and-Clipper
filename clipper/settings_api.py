"""Settings page: edit the main options, and show an environment check for troubleshooting."""
import importlib.metadata
import logging
import os
import shutil
import subprocess
import sys

from flask import Blueprint, jsonify, render_template, request

import config
from . import llm, video

log = logging.getLogger("clipper.settings_api")
bp = Blueprint("settings", __name__)

LABELS = {
    "WHISPER_MODEL": ("Speech recognition model",
                      "Bigger is more accurate but slower. The .en models are English only."),
    "WHISPER_DEVICE": ("Speech recognition device", "auto tries the GPU first and falls back to the CPU."),
    "WHISPER_GPU_COMPUTE": ("GPU precision", "int8_float16 uses the least GPU memory (best for 6 GB)."),
    "WHISPER_LANGUAGE": ("Spoken language code", "For example en. Leave empty to detect it automatically "
                                                 "(needs a model without .en)."),
    "LLM_MODEL": ("AI model for highlights and campaigns", "Must be a local Ollama model, for example qwen3:4b."),
    "OLLAMA_URL": ("Ollama address", "Normally http://127.0.0.1:11434"),
    "LLM_CONTEXT": ("AI memory per request (tokens)", "Bigger needs more GPU memory."),
    "VERTICAL_SIZE": ("Vertical video size", "Used for all vertical layouts."),
    "MAX_UPLOAD_GB": ("Largest upload allowed (GB)", ""),
    "MODEL_DIR": ("Folder for downloaded models", "Full path. Models already downloaded are not moved."),
}


def err(message, code=400):
    return jsonify(error=message), code


@bp.get("/settings")
def settings_page():
    return render_template("settings.html")


@bp.get("/api/settings")
def get_settings():
    running = config.current_settings()
    saved = {**running, **config.load_saved()}
    fields = []
    for name, (default, kind, rule) in config.SETTINGS.items():
        label, help_text = LABELS.get(name, (name, ""))
        field = {"name": name, "label": label, "help": help_text, "kind": kind,
                 "default": default, "saved": saved[name], "running": running[name]}
        if kind == "choice":
            field["choices"] = rule
        elif kind == "int":
            field["min"], field["max"] = rule
        fields.append(field)
    return jsonify(fields=fields, file=str(config.SETTINGS_PATH))


@bp.put("/api/settings")
def save_settings():
    try:
        clean = config.validate_settings((request.get_json(silent=True) or {}).get("values") or {})
    except ValueError as e:
        return err(str(e))
    config.save_settings(clean)
    return jsonify(ok=True)


@bp.post("/api/settings/reset")
def reset_settings():
    config.SETTINGS_PATH.unlink(missing_ok=True)
    return jsonify(ok=True)


# ---------- environment check ----------

def _gpu():
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.used,memory.total,temperature.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5, creationflags=video.NO_WINDOW)
        if r.returncode != 0 or not r.stdout.strip():
            return None
        name, used, total, temp = [x.strip() for x in r.stdout.splitlines()[0].split(",")]
        return f"{name}: {used} of {total} MiB in use, {temp} C"
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None


def _folder_size(path):
    total = 0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


@bp.get("/api/system")
def system_check():
    checks = []

    def add(name, ok, detail):
        checks.append({"name": name, "ok": ok, "detail": detail})

    add("Python", True, sys.version.split()[0])
    ffmpeg = shutil.which("ffmpeg")
    add("FFmpeg", bool(ffmpeg), ffmpeg or "Not found. Install FFmpeg and restart VS Code.")
    if ffmpeg:
        try:
            nv = video.nvenc_available()
            add("GPU video encoding (NVENC)", nv,
                "Available." if nv else "Not available. Clips will be encoded on the CPU (slower).")
        except video.VideoError as e:
            add("GPU video encoding (NVENC)", False, str(e))
    gpu = _gpu()
    add("NVIDIA GPU", bool(gpu), gpu or "nvidia-smi found no GPU. Transcription will use the CPU.")

    st = llm.status()
    add("Ollama and AI model", bool(st["running"] and st["installed"]), st["message"])

    for label, module, dist in (("OpenCV (face tracking)", "cv2", "opencv-python-headless"),
                                ("faster-whisper (transcription)", "faster_whisper", "faster-whisper")):
        try:
            importlib.import_module(module)
            try:
                version = importlib.metadata.version(dist)
            except importlib.metadata.PackageNotFoundError:
                version = "installed"
            add(label, True, version)
        except ImportError:
            add(label, False, "Not installed. Run: pip install -r requirements.txt")

    free_gb = shutil.disk_usage(config.DATA_DIR).free / 1024**3
    used_gb = _folder_size(config.DATA_DIR) / 1024**3
    add("Disk space", free_gb > 10,
        f"{free_gb:.0f} GB free. The app's data folder uses {used_gb:.1f} GB ({config.DATA_DIR}).")
    return jsonify(checks=checks)