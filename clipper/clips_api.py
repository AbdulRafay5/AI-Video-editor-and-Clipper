"""Clip editing routes: rename, change times/layout, re-render, a quick preview of the original
footage, and approving or dismissing AI highlight suggestions."""
import logging
import subprocess
import time
from pathlib import Path

from flask import Blueprint, jsonify, request, send_file

import config
from . import db, jobs
from . import layout as lay
from . import video

log = logging.getLogger("clipper.clips_api")
bp = Blueprint("clips", __name__)

PREVIEW_DIR = config.DATA_DIR / "previews"
PREVIEW_DIR.mkdir(parents=True, exist_ok=True)

ACTIVE = ("queued", "processing")
HIGHLIGHT_STATUSES = ("suggested", "approved", "rejected")


def err(message, code=400):
    return jsonify(error=message), code


def _remove_file(path):
    """Delete a file, retrying briefly: Windows keeps a video locked while a browser is playing it."""
    p = Path(path)
    for _ in range(6):
        try:
            p.unlink(missing_ok=True)
            return True
        except PermissionError:
            time.sleep(0.25)
    return False


@bp.put("/api/clips/<int:cid>")
def update_clip(cid):
    clip = db.query_one(
        "SELECT c.*, p.duration AS video_duration FROM clips c "
        "JOIN projects p ON p.id = c.project_id WHERE c.id=?", (cid,))
    if not clip:
        return err("Clip not found.", 404)
    if clip["status"] in ACTIVE:
        return err("Cancel this clip first, then edit it.", 409)

    body = request.get_json(silent=True) or {}
    name = str(body.get("name") if body.get("name") is not None else clip["name"]).strip()[:120] or clip["name"]
    try:
        start = float(body.get("start", clip["start_time"]))
        end = float(body.get("end", clip["end_time"]))
    except (TypeError, ValueError):
        return err("Start and end must be numbers.")
    start = max(start, 0.0)
    end = min(end, clip["video_duration"])
    if end - start < 1:
        return err("A clip must be at least 1 second long and inside the video.")
    if end - start > config.MAX_CLIP_SECONDS:
        return err(f"A clip can be at most {config.MAX_CLIP_SECONDS} seconds long.")

    layout, pos = lay.normalise(body.get("layout", clip["layout"]), body.get("crop_pos", clip["crop_pos"]))
    burn = 1 if body.get("burn_captions", clip["burn_captions"]) else 0
    if burn and not db.query_one("SELECT project_id FROM captions WHERE project_id=?", (clip["project_id"],)):
        return err("Generate captions for this project first (section 5), or untick the captions option.")

    render_changed = (
        abs(start - clip["start_time"]) > 0.001 or abs(end - clip["end_time"]) > 0.001
        or layout != clip["layout"] or abs(pos - clip["crop_pos"]) > 0.001 or burn != clip["burn_captions"]
    )
    if not render_changed:
        db.execute("UPDATE clips SET name=? WHERE id=?", (name, cid))
        return jsonify(ok=True, rendering=False)

    if clip["output_path"] and not _remove_file(clip["output_path"]):
        return err("The old video is still open in the browser. Close its player and try again.", 409)
    db.execute(
        "UPDATE clips SET name=?, start_time=?, end_time=?, layout=?, crop_pos=?, burn_captions=?, "
        "status='queued', progress=0, error=NULL, note=NULL, output_path=NULL, encoder=NULL WHERE id=?",
        (name, start, end, layout, pos, burn, cid))
    db.execute("DELETE FROM clip_compliance WHERE clip_id=?", (cid,))
    jobs.submit_clip(cid)
    return jsonify(ok=True, rendering=True)


@bp.get("/api/clips/<int:cid>/original-preview")
def original_preview(cid):
    """A small, fast preview of the uncut source footage for this clip's time range."""
    clip = db.query_one(
        "SELECT c.start_time, c.end_time, p.source_path FROM clips c "
        "JOIN projects p ON p.id = c.project_id WHERE c.id=?", (cid,))
    if not clip:
        return err("Clip not found.", 404)

    start, end = clip["start_time"], clip["end_time"]
    out = PREVIEW_DIR / f"clip_{cid}_{int(start * 100)}_{int(end * 100)}.mp4"
    if not out.exists():
        for old in PREVIEW_DIR.glob(f"clip_{cid}_*"):
            _remove_file(old)
        tmp = out.with_name(out.stem + ".part.mp4")
        try:
            cmd = [video._tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
                   "-ss", f"{start:.3f}", "-i", str(Path(clip["source_path"]).resolve()),
                   "-t", f"{end - start:.3f}",
                   "-vf", "scale=-2:360", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30",
                   "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart",
                   str(tmp)]
            r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                               timeout=300, creationflags=video.NO_WINDOW)
        except subprocess.TimeoutExpired:
            tmp.unlink(missing_ok=True)
            return err("Preparing the original footage took too long.", 500)
        except video.VideoError as e:
            return err(str(e), 500)
        if r.returncode != 0 or not tmp.exists() or tmp.stat().st_size < 1024:
            tmp.unlink(missing_ok=True)
            log.warning("Original preview failed: %s", (r.stderr or "").strip()[-300:])
            return err("Could not prepare the original footage preview.", 500)
        tmp.replace(out)
    return send_file(out, mimetype="video/mp4", conditional=True)


@bp.put("/api/highlights/<int:hid>/status")
def set_highlight_status(hid):
    if not db.query_one("SELECT id FROM highlights WHERE id=?", (hid,)):
        return err("Highlight not found.", 404)
    status = (request.get_json(silent=True) or {}).get("status")
    if status not in HIGHLIGHT_STATUSES:
        return err("Status must be suggested, approved or rejected.")
    db.execute("UPDATE highlights SET status=? WHERE id=?", (status, hid))
    return jsonify(ok=True, status=status)