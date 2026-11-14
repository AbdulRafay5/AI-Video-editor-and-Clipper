"""Local, zero-cost B-roll media library. Stored per project, never sent to a cloud service."""
import uuid
import json
import threading
from pathlib import Path

from flask import Blueprint, jsonify, request, send_file

import config
from . import db

bp = Blueprint("broll", __name__)
MEDIA_ROOT = config.DATA_DIR / "broll"
VIDEO_TYPES = {".mp4", ".mov", ".mkv", ".webm"}
IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".webp"}
ALLOWED = VIDEO_TYPES | IMAGE_TYPES
MAX_BYTES = 200 * 1024 * 1024
_meta_lock = threading.RLock()


def _metadata(pid):
    path = _folder(pid) / "metadata.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_metadata(pid, data):
    folder = _folder(pid)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / "metadata.json"
    temp = folder / ("meta_" + uuid.uuid4().hex + ".tmp")
    try:
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(target)
    finally:
        temp.unlink(missing_ok=True)


def _label(text):
    return " ".join(str(text or "").split())[:160]




def _project(pid):
    return db.query_one("SELECT id FROM projects WHERE id=?", (pid,))


def _folder(pid):
    return MEDIA_ROOT / str(pid)


def _item(pid, item_id):
    # Only allow generated lowercase hex IDs; never use a user path.
    if len(item_id) != 32 or any(c not in "0123456789abcdef" for c in item_id):
        return None
    folder = _folder(pid)
    for suffix in ALLOWED:
        candidate = folder / (item_id + suffix)
        if candidate.is_file():
            return candidate
    return None


@bp.get("/api/projects/<int:pid>/broll")
def list_broll(pid):
    if not _project(pid):
        return jsonify(error="Project not found."), 404
    folder = _folder(pid)
    items = []
    with _meta_lock:
        metadata = _metadata(pid)
    if folder.is_dir():
        for p in folder.iterdir():
            if p.is_file() and p.suffix in ALLOWED and len(p.stem) == 32:
                items.append({"id": p.stem, "kind": "video" if p.suffix in VIDEO_TYPES else "image",
                              "name": metadata.get(p.stem, {}).get("name", p.name),
                              "description": metadata.get(p.stem, {}).get("description", ""),
                              "bytes": p.stat().st_size,
                              "url": f"/api/projects/{pid}/broll/{p.stem}/file"})
    return jsonify(items=sorted(items, key=lambda item: item["name"]))


@bp.post("/api/projects/<int:pid>/broll")
def add_broll(pid):
    if not _project(pid):
        return jsonify(error="Project not found."), 404
    upload = request.files.get("file")
    if not upload or not upload.filename:
        return jsonify(error="Choose an image or video to upload."), 400
    suffix = Path(upload.filename).suffix.lower()
    if suffix not in ALLOWED:
        return jsonify(error="Unsupported format. Use MP4, MOV, MKV, WebM, PNG, JPG or WebP."), 400
    folder = _folder(pid)
    folder.mkdir(parents=True, exist_ok=True)
    destination = folder / (uuid.uuid4().hex + suffix)
    size = 0
    try:
        with destination.open("wb") as output:
            while True:
                chunk = upload.stream.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_BYTES:
                    raise ValueError("B-roll files must be 200 MB or smaller.")
                output.write(chunk)
        if not size:
            raise ValueError("The uploaded file was empty.")
    except (OSError, ValueError) as exc:
        destination.unlink(missing_ok=True)
        return jsonify(error=str(exc)), 400
    name = _label(Path(upload.filename.replace("\\\\", "/")).name) or destination.name
    description = _label(request.form.get("description"))
    with _meta_lock:
        meta = _metadata(pid)
        meta[destination.stem] = {"name": name, "description": description}
        _save_metadata(pid, meta)
    return jsonify(id=destination.stem, name=name, description=description, bytes=size), 201


@bp.get("/api/projects/<int:pid>/broll/<item_id>/file")
def broll_file(pid, item_id):
    if not _project(pid):
        return jsonify(error="Project not found."), 404
    path = _item(pid, item_id)
    if not path:
        return jsonify(error="B-roll file not found."), 404
    return send_file(path, conditional=True)


@bp.delete("/api/projects/<int:pid>/broll/<item_id>")
def delete_broll(pid, item_id):
    if not _project(pid):
        return jsonify(error="Project not found."), 404
    path = _item(pid, item_id)
    if not path:
        return jsonify(error="B-roll file not found."), 404
    path.unlink()
    with _meta_lock:
        meta = _metadata(pid)
        meta.pop(item_id, None)
        _save_metadata(pid, meta)
    return jsonify(ok=True)


@bp.patch("/api/projects/<int:pid>/broll/<item_id>")
def label_broll(pid, item_id):
    if not _project(pid):
        return jsonify(error="Project not found."), 404
    if not _item(pid, item_id):
        return jsonify(error="B-roll file not found."), 404
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict) or not isinstance(body.get("description"), str):
        return jsonify(error="Provide a description."), 400
    with _meta_lock:
        data = _metadata(pid)
        old = data.get(item_id, {})
        data[item_id] = {"name": old.get("name", item_id), "description": _label(body["description"])}
        _save_metadata(pid, data)
    return jsonify(ok=True, description=data[item_id]["description"])
