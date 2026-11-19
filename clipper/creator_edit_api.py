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
    "music_ducking": False,
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
    if type(value["music_ducking"]) is not bool:
        raise EditError("Automatic music ducking must be enabled or disabled.")
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


def scene_assignments(pid, slots):
    """Return manual overrides, ignoring stale slot IDs after script edits."""
    row = db.query_one(
        "SELECT mapping_json FROM creator_scene_assignments WHERE project_id=?", (pid,))
    saved = json.loads(row["mapping_json"]) if row else {}
    if not isinstance(saved, dict):
        return {}
    return {target: source for target, source in saved.items()
            if target in slots and source in slots and isinstance(source, str)}


@bp.get("/api/creator/projects/<int:pid>/edit/scene-assignments")
def get_scene_assignments(pid):
    project = db.query_one("SELECT script_json FROM creator_projects WHERE id=?", (pid,))
    if not project:
        return jsonify(error="Project not found."), 404
    if not project["script_json"]:
        return jsonify(error="Create a script before assigning scene footage."), 409
    from . import creator_render_api as creator
    slots = creator._slots(json.loads(project["script_json"]))
    return jsonify(slots=slots, assignments=scene_assignments(pid, slots))


@bp.put("/api/creator/projects/<int:pid>/edit/scene-assignments")
def set_scene_assignments(pid):
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or set(data) != {"assignments"}:
        return jsonify(error="Provide a scene-to-footage assignment object."), 400
    assignments = data["assignments"]
    if not isinstance(assignments, dict):
        return jsonify(error="Assignments must be an object."), 400
    from . import creator_render_api as creator
    with creator._upload_lock:
        with db.get_db() as conn:
            project = conn.execute(
                "SELECT script_json, status FROM creator_projects WHERE id=?", (pid,)).fetchone()
            if not project:
                return jsonify(error="Project not found."), 404
            if project["status"] in creator.BUSY:
                return jsonify(error="Finish the active generation stage first."), 409
            if not project["script_json"]:
                return jsonify(error="Create a script first."), 409
            slots = creator._slots(json.loads(project["script_json"]))
            if any(not isinstance(target, str) or target not in slots
                   or not isinstance(source, str) or source not in slots
                   for target, source in assignments.items()):
                return jsonify(error="Scene assignments must use existing project slots."), 400
            # An explicit self-assignment must override reverse/shuffle too.
            cleaned = dict(assignments)
            previous = conn.execute(
                "SELECT mapping_json FROM creator_scene_assignments "
                "WHERE project_id=?", (pid,)).fetchone()
            old = json.loads(previous["mapping_json"]) if previous else {}
            changed = old != cleaned
            conn.execute(
                "INSERT INTO creator_scene_assignments(project_id,mapping_json) VALUES (?,?) "
                "ON CONFLICT(project_id) DO UPDATE SET mapping_json=excluded.mapping_json,"
                "updated_at=CURRENT_TIMESTAMP", (pid, json.dumps(cleaned, sort_keys=True)))
            if changed:
                conn.execute(
                    "UPDATE creator_stages SET status='pending',artifact_json=NULL,error=NULL "
                    "WHERE project_id=? AND stage='render'", (pid,))
    return jsonify(assignments=cleaned, changed=changed)


def validate_scene_effects(effects, slots):
    """Only allow conservative, independent video effects for project slots."""
    if not isinstance(effects, dict):
        raise EditError("Scene effects must be an object.")
    if set(effects) - set(slots):
        raise EditError("Effects reference a scene that does not exist.")
    cleaned = {}
    for slot, effect in effects.items():
        if not isinstance(effect, dict) or not effect or set(effect) - {"speed", "fit"}:
            raise EditError("Each scene effect must set speed, fit or both.")
        item = {}
        if "speed" in effect:
            speed = effect["speed"]
            if type(speed) not in (int, float) or not math.isfinite(speed) or not 0.5 <= speed <= 2.0:
                raise EditError("Scene footage speed must be between 0.5x and 2x.")
            item["speed"] = float(speed)
        if "fit" in effect:
            if effect["fit"] not in FITS:
                raise EditError("Scene fitting must be cover or contain.")
            item["fit"] = effect["fit"]
        cleaned[slot] = item
    return cleaned


def scene_effects(pid, slots):
    row = db.query_one("SELECT effects_json FROM creator_scene_effects WHERE project_id=?", (pid,))
    if not row:
        return {}
    saved = json.loads(row["effects_json"])
    if not isinstance(saved, dict):
        return {}
    # Retain valid current slots after the script has been edited.
    return {slot: options for slot, options in saved.items()
            if slot in slots and isinstance(options, dict)
            and not (set(options) - {"speed", "fit"})}


@bp.get("/api/creator/projects/<int:pid>/edit/scene-effects")
def get_scene_effects(pid):
    project = db.query_one("SELECT script_json FROM creator_projects WHERE id=?", (pid,))
    if not project:
        return jsonify(error="Project not found."), 404
    if not project["script_json"]:
        return jsonify(error="Create a script before editing scene footage."), 409
    from . import creator_render_api as creator
    slots = creator._slots(json.loads(project["script_json"]))
    return jsonify(slots=slots, effects=scene_effects(pid, slots))


@bp.put("/api/creator/projects/<int:pid>/edit/scene-effects")
def set_scene_effects(pid):
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or set(data) != {"effects"}:
        return jsonify(error="Provide a scene effects object."), 400
    from . import creator_render_api as creator
    with creator._upload_lock:
        with db.get_db() as conn:
            project = conn.execute(
                "SELECT script_json,status FROM creator_projects WHERE id=?", (pid,)).fetchone()
            if not project:
                return jsonify(error="Project not found."), 404
            if project["status"] in creator.BUSY:
                return jsonify(error="Finish the active generation stage first."), 409
            if not project["script_json"]:
                return jsonify(error="Create a script first."), 409
            slots = creator._slots(json.loads(project["script_json"]))
            try:
                cleaned = validate_scene_effects(data["effects"], slots)
            except (EditError, TypeError, ValueError) as exc:
                return jsonify(error=str(exc)), 400
            previous = conn.execute(
                "SELECT effects_json FROM creator_scene_effects WHERE project_id=?", (pid,)).fetchone()
            old = json.loads(previous["effects_json"]) if previous else {}
            changed = old != cleaned
            conn.execute(
                "INSERT INTO creator_scene_effects(project_id,effects_json) VALUES (?,?) "
                "ON CONFLICT(project_id) DO UPDATE SET effects_json=excluded.effects_json,"
                "updated_at=CURRENT_TIMESTAMP", (pid, json.dumps(cleaned, sort_keys=True)))
            if changed:
                conn.execute(
                    "UPDATE creator_stages SET status='pending',artifact_json=NULL,error=NULL "
                    "WHERE project_id=? AND stage='render'", (pid,))
    return jsonify(effects=cleaned, changed=changed)


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
