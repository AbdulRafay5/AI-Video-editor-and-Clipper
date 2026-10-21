import json
import logging
import shutil
import subprocess
import threading
from collections import deque
from pathlib import Path

log = logging.getLogger("clipper.video")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class VideoError(Exception):
    """A problem with a video file or an FFmpeg run, with a user-readable message."""


class Cancelled(Exception):
    """The user cancelled the job."""


def _tool(name):
    path = shutil.which(name)
    if not path:
        raise VideoError(f"{name} was not found. Install FFmpeg and restart VS Code.")
    return path


def probe(path):
    cmd = [_tool("ffprobe"), "-v", "error", "-print_format", "json",
           "-show_format", "-show_streams", str(path)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=60, creationflags=NO_WINDOW)
    except subprocess.TimeoutExpired:
        raise VideoError("Reading this file took too long; it may be corrupt.")
    if r.returncode != 0:
        raise VideoError("This file can't be read as a video: " + r.stderr.strip()[:300])
    data = json.loads(r.stdout or "{}")
    streams = data.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    if not v:
        raise VideoError("No video stream found in this file.")
    duration = float(data.get("format", {}).get("duration") or v.get("duration") or 0)
    if duration <= 0:
        raise VideoError("Could not determine the video's duration.")

    width, height = int(v.get("width") or 0), int(v.get("height") or 0)
    # Phone videos are often stored sideways with a rotation flag; FFmpeg rotates
    # them automatically, so report the size as it will actually appear.
    rotation = 0
    for sd in v.get("side_data_list") or []:
        if "rotation" in sd:
            try:
                rotation = int(float(sd["rotation"]))
            except (TypeError, ValueError):
                pass
    if not rotation:
        try:
            rotation = int(float((v.get("tags") or {}).get("rotate", 0)))
        except (TypeError, ValueError):
            rotation = 0
    if abs(rotation) % 180 == 90:
        width, height = height, width

    return {
        "duration": duration,
        "width": width,
        "height": height,
        "has_audio": any(s.get("codec_type") == "audio" for s in streams),
    }


_nvenc_ok = None
_nvenc_lock = threading.Lock()


def nvenc_available():
    """True only if a tiny test encode with h264_nvenc actually works."""
    global _nvenc_ok
    with _nvenc_lock:
        if _nvenc_ok is None:
            cmd = [_tool("ffmpeg"), "-hide_banner", "-loglevel", "error",
                   "-f", "lavfi", "-i", "color=c=black:s=256x256:d=0.2",
                   "-c:v", "h264_nvenc", "-f", "null", "-"]
            try:
                r = subprocess.run(cmd, capture_output=True, timeout=30,
                                   creationflags=NO_WINDOW)
                _nvenc_ok = r.returncode == 0
            except Exception:
                _nvenc_ok = False
            log.info("NVENC available: %s", _nvenc_ok)
        return _nvenc_ok


def cut_clip(src, start, end, out_path, encoder, on_progress, cancel_event, vf=None, work_dir=None):
    """Re-encode [start, end] of src into out_path. Re-encoding (not stream copy)
    gives frame-accurate cuts and a consistent H.264/AAC MP4.

    vf is an optional FFmpeg video filter (vertical layout and/or captions).
    If captions are used, work_dir holds captions.ass and a fonts folder, and FFmpeg
    runs inside it so the filter can use plain relative paths (no Windows escaping)."""
    duration = end - start
    if encoder == "nvenc":
        venc = ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "21", "-b:v", "0"]
    else:
        venc = ["-c:v", "libx264", "-preset", "medium", "-crf", "20"]
    vf_args = ["-vf", vf] if vf else []

    cmd = [_tool("ffmpeg"), "-hide_banner", "-y",
           "-ss", f"{start:.3f}", "-i", str(Path(src).resolve()), "-t", f"{duration:.3f}",
           "-map", "0:v:0", "-map", "0:a:0?",
           *vf_args, *venc, "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "192k",
           "-movflags", "+faststart",
           "-progress", "pipe:1", "-nostats",
           str(Path(out_path).resolve())]
    log.info("FFmpeg: %s", " ".join(cmd))

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace",
                            cwd=str(work_dir) if work_dir else None,
                            creationflags=NO_WINDOW)
    tail = deque(maxlen=15)
    try:
        for line in proc.stdout:
            if cancel_event.is_set():
                proc.terminate()
                break
            line = line.strip()
            if line.startswith(("out_time_us=", "out_time_ms=")):
                try:
                    micro = int(line.split("=", 1)[1])
                    on_progress(max(0.0, min(micro / 1e6 / duration, 1.0)))
                except ValueError:
                    pass
            elif "=" not in line:
                tail.append(line)
    finally:
        proc.wait()

    if cancel_event.is_set():
        Path(out_path).unlink(missing_ok=True)
        raise Cancelled()
    if proc.returncode != 0:
        Path(out_path).unlink(missing_ok=True)
        raise VideoError("FFmpeg failed: " + " | ".join(tail)[-400:])


def verify_output(path, expected_duration, expected_size=None):
    """Confirm the file really exists and looks right before reporting success."""
    p = Path(path)
    if not p.exists() or p.stat().st_size < 1024:
        raise VideoError("Export finished but the output file is missing or empty.")
    info = probe(p)
    tolerance = max(1.0, expected_duration * 0.05)
    if abs(info["duration"] - expected_duration) > tolerance:
        raise VideoError(
            f"Output length {info['duration']:.1f}s doesn't match the expected "
            f"{expected_duration:.1f}s.")
    if expected_size and (info["width"], info["height"]) != tuple(expected_size):
        raise VideoError(
            f"Output size {info['width']}x{info['height']} doesn't match the expected "
            f"{expected_size[0]}x{expected_size[1]}.")
    return info