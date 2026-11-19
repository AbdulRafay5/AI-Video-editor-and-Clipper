"""Creator Studio advanced video editing controls.

An opt-in adapter around MoneyPrinterTurbo's immutable revision renderer.
Its original module, clipper routes and saved project revisions are untouched.

Options apply to footage; spoken script/audio and subtitle chronology stay
unchanged. FFmpeg prepares speed-adjusted or contained files on local disk.
"""
import hashlib
import json
import math
import os
import random
import shutil
import subprocess
import uuid
from pathlib import Path

from flask import Blueprint, jsonify, request

from . import db

bp = Blueprint("creator_edit", __name__)

FORMATS = {
    "portrait": (720, 1280),
    "landscape": (1280, 720),
    "square": (1080, 1080),
}
FITS = ("cover", "contain")
ORDERS = ("script", "reverse", "shuffle")
DEFAULTS = {
    "format": "portrait",
    "fit": "cover",
    "speed": 1.0,
    "order": "script",
    "narration_volume": 1.0,
    "bgm_volume": 0.15,
}
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class EditError(ValueError):
    pass


def validate(value):
    required = {"format", "fit", "speed", "order"}
    if (not isinstance(value, dict) or not required.issubset(value)
            or set(value) - set(DEFAULTS)):
        raise EditError("Provide format, fit, speed and order; optional audio levels are supported.")
    value = {**DEFAULTS, **value}
    if value["format"] not in FORMATS:
        raise EditError("Choose portrait, landscape or square.")
    if value["fit"] not in FITS:
        raise EditError("Choose crop-to-fill or letterbox.")
    if value["order"] not in ORDERS:
        raise EditError("Choose script order, reverse or shuffle.")
    speed = value["speed"]
    if type(speed) not in (int, float) or not math.isfinite(speed) or not 0.5 <= speed <= 2.0:
        raise EditError("Footage speed must be between 0.5x and 2x.")
    narration_volume = value["narration_volume"]
    bgm_volume = value["bgm_volume"]
    if (type(narration_volume) not in (int, float)
            or not math.isfinite(narration_volume)
            or not 0.0 <= narration_volume <= 2.0):
        raise EditError("Narration volume must be 0–200%.")
    if (type(bgm_volume) not in (int, float)
            or not math.isfinite(bgm_volume)
            or not 0.0 <= bgm_volume <= 1.0):
        raise EditError("Background music volume must be 0–100%.")
    return {**value, "speed": float(speed),
            "narration_volume": float(narration_volume),
            "bgm_volume": float(bgm_volume)}


def options(pid):
    row = db.query_one(
        "SELECT options_json FROM creator_edit_settings WHERE project_id=?", (pid,))
    if not row:
        return dict(DEFAULTS)
    return validate(json.loads(row["options_json"]))


@bp.get("/api/creator/projects/<int:pid>/edit/options")
def get_edit_options(pid):
    if not db.query_one("SELECT id FROM creator_projects WHERE id=?", (pid,)):
        return jsonify(error="Project not found."), 404
    return jsonify(options=options(pid), formats={
        key: {"width": dims[0], "height": dims[1]} for key, dims in FORMATS.items()
    })


@bp.put("/api/creator/projects/<int:pid>/edit/options")
def set_edit_options(pid):
    try:
        new = validate(request.get_json(silent=True))
    except (EditError, TypeError, ValueError) as exc:
        return jsonify(error=str(exc)), 400
    from . import creator_render_api as creator
    with creator._upload_lock:
        with db.get_db() as conn:
            project = conn.execute("SELECT status FROM creator_projects WHERE id=?", (pid,)).fetchone()
            if not project:
                return jsonify(error="Project not found."), 404
            if project["status"] in creator.BUSY:
                return jsonify(error="Finish the current production stage first."), 409
            previous = conn.execute(
                "SELECT options_json FROM creator_edit_settings WHERE project_id=?", (pid,)).fetchone()
            changed = not previous or json.loads(previous["options_json"]) != new
            conn.execute(
                "INSERT INTO creator_edit_settings(project_id, options_json) VALUES (?,?) "
                "ON CONFLICT(project_id) DO UPDATE SET options_json=excluded.options_json, "
                "updated_at=CURRENT_TIMESTAMP", (pid, json.dumps(new)))
            if changed:
                conn.execute(
                    "UPDATE creator_stages SET status='pending', artifact_json=NULL, error=NULL "
                    "WHERE project_id=? AND stage='render'", (pid,))
    return jsonify(options=new, changed=changed)


def footage_mapping(slots, pid, order):
    if order not in ORDERS:
        raise EditError("Unknown footage order.")
    inputs = list(slots)
    if order == "reverse":
        inputs.reverse()
    if order == "shuffle":
        random.Random(pid).shuffle(inputs)
    return dict(zip(slots, inputs))


def prepare_footage(pid, source, slot, current):
    """Return a cached, safe FFmpeg-prepared clip for local rendering.

    Source comes solely from server-validated Creator visual assets, never
    a user-submitted filesystem path. Media files aren't edited in place.
    """
    source = Path(source)
    if current["fit"] == "cover" and current["speed"] == 1.0:
        return source
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise EditError("Install FFmpeg to use advanced footage controls.")
    width, height = FORMATS[current["format"]]
    speed = current["speed"]
    if current["fit"] == "contain":
        aspect_filter = (
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,setsar=1"
        )
    else:
        aspect_filter = (
            f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},setsar=1"
        )
    filter_chain = f"setpts=(PTS-STARTPTS)/{speed:.6f},{aspect_filter},fps=30"
    stat = source.stat()
    identity = {
        "source": str(source.resolve()), "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns, "format": current["format"],
        "fit": current["fit"], "speed": speed,
    }
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:32]
    from . import creator_render_api as creator
    destination_dir = creator._root(pid) / "prepared_footage"
    destination_dir.mkdir(parents=True, exist_ok=True)
    out = destination_dir / f"prepared_{digest}.mp4"
    if out.is_file() and out.stat().st_size > 1024:
        return out
    temp = destination_dir / f".{uuid.uuid4().hex}.mp4"
    command = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
        "-i", str(source), "-map", "0:v:0", "-an", "-vf", filter_chain,
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-movflags", "+faststart", str(temp),
    ]
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=600,
            creationflags=NO_WINDOW)
        if result.returncode or not temp.is_file() or temp.stat().st_size < 1024:
            raise EditError("Couldn't prepare footage: " + result.stderr[-260:])
        os.replace(temp, out)
        return out
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise EditError("Footage preparation failed or exceeded the local time limit.") from exc
    finally:
        temp.unlink(missing_ok=True)
