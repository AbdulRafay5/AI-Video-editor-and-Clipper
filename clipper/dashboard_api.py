"""Dashboard and history: totals, projects, recent clips, disk usage and safe cleanup."""
import logging
import os
import shutil

from flask import Blueprint, jsonify, render_template, request

import config
from . import db

log = logging.getLogger("clipper.dashboard_api")
bp = Blueprint("dashboard", __name__)

PREVIEW_DIR = config.DATA_DIR / "previews"
EXPORT_DIR = config.DATA_DIR / "exports"

# Folders that only hold files the app can make again.
CLEANABLE = {
    "work": (config.WORK_DIR, "Temporary render files"),
    "previews": (PREVIEW_DIR, "Original-footage previews"),
    "exports": (EXPORT_DIR, "Export zips"),
    "audio": (config.AUDIO_DIR, "Temporary transcription audio"),
}

PROJECTS_SQL = """
SELECT p.id, p.name, p.original_filename, p.duration, p.size_bytes, p.width, p.height,
       p.created_at, p.layout,
       (SELECT name FROM campaigns WHERE id = p.campaign_id) AS campaign,
       (SELECT COUNT(*) FROM clips c WHERE c.project_id = p.id) AS clips_total,
       (SELECT COUNT(*) FROM clips c WHERE c.project_id = p.id AND c.status = 'done') AS clips_done,
       (SELECT COUNT(*) FROM clips c WHERE c.project_id = p.id AND c.status = 'failed') AS clips_failed,
       (SELECT status FROM transcripts t WHERE t.project_id = p.id) AS transcript,
       (SELECT COUNT(*) FROM captions ca WHERE ca.project_id = p.id) AS has_captions
FROM projects p ORDER BY p.id DESC
"""


def err(message, code=400):
    return jsonify(error=message), code


def _size(path):
    """Total bytes of a file or folder (0 if it doesn't exist)."""
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    total = 0
    for root, _, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


def _running_jobs():
    total = 0
    for table in ("clips", "transcripts", "analyses"):
        row = db.query_one(f"SELECT COUNT(*) AS n FROM {table} WHERE status IN ('queued','processing')")
        total += row["n"]
    return total


def _inside_data(path):
    data = config.DATA_DIR.resolve()
    resolved = path.resolve()
    return resolved == data or data in resolved.parents


@bp.get("/dashboard")
def dashboard_page():
    return render_template("dashboard.html")


@bp.get("/api/dashboard")
def dashboard():
    projects = db.query(PROJECTS_SQL)
    recent = db.query(
        "SELECT c.id, c.name, c.status, c.created_at, c.project_id, p.name AS project "
        "FROM clips c JOIN projects p ON p.id = c.project_id ORDER BY c.id DESC LIMIT 10")
    totals = {
        "projects": len(projects),
        "clips_done": sum(p["clips_done"] for p in projects),
        "clips_failed": sum(p["clips_failed"] for p in projects),
        "campaigns": db.query_one("SELECT COUNT(*) AS n FROM campaigns")["n"],
    }

    parts = [
        ("uploads", "Uploaded videos", config.UPLOAD_DIR, False),
        ("outputs", "Finished clips", config.OUTPUT_DIR, False),
        ("models", "Speech and face models", config.MODEL_DIR, False),
    ] + [(key, label, folder, True) for key, (folder, label) in CLEANABLE.items()]
    disk, counted = [], 0
    for key, label, folder, cleanable in parts:
        size = _size(folder)
        if _inside_data(folder):
            counted += size
        disk.append({"key": key, "label": label, "bytes": size, "cleanable": cleanable})
    other = max(_size(config.DATA_DIR) - counted, 0)   # database, logs, settings
    disk.append({"key": "other", "label": "Database, logs and settings", "bytes": other, "cleanable": False})

    return jsonify(totals=totals, projects=projects, recent=recent, disk=disk,
                   free_bytes=shutil.disk_usage(config.DATA_DIR).free, running=_running_jobs())


@bp.post("/api/cleanup")
def cleanup():
    targets = (request.get_json(silent=True) or {}).get("targets") or []
    unknown = [t for t in targets if t not in CLEANABLE]
    if unknown or not targets:
        return err("Choose which temporary files to remove.")
    if _running_jobs():
        return err("Wait until the running jobs have finished, then try again.", 409)

    freed = 0
    for key in targets:
        folder = CLEANABLE[key][0]
        if not folder.is_dir():
            continue
        for item in folder.iterdir():
            try:
                size = _size(item)
                if item.is_dir():
                    shutil.rmtree(item)
                else:
                    item.unlink()
                freed += size
            except OSError:
                log.warning("Could not remove %s", item)
    return jsonify(freed=freed)