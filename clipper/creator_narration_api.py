"""Creator Studio narration stage: durable per-scene audio and precise offsets."""
import json
import logging
import io
import math
import tempfile
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from flask import Blueprint, jsonify, request, send_file

import config
from . import db, local_narration

bp = Blueprint("creator_narration", __name__)
log = logging.getLogger("clipper.creator_narration")
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="creator-voice")
_preview_lock = threading.Lock()
_PREVIEW_TEXT = "Welcome! Here is a short preview of this narration voice."


def _project(pid):
    return db.query_one("SELECT id, status, script_json FROM creator_projects WHERE id=?", (pid,))


def _root(pid):
    return config.DATA_DIR / "creator" / str(pid) / "narration"


def _stage(pid):
    row = db.query_one("SELECT status, artifact_json, error FROM creator_stages "
                       "WHERE project_id=? AND stage='narration'", (pid,))
    if not row:
        return None
    artifact = json.loads(row["artifact_json"]) if row["artifact_json"] else None
    return {"status": row["status"], "error": row["error"], "artifact": artifact}


def _segments(script):
    segments = [{"kind": "hook", "text": script["hook"]}]
    for i, scene in enumerate(script["scenes"]):
        segments.append({"kind": "scene", "scene_index": i, "text": scene["narration"]})
    segments.append({"kind": "closing", "text": script["closing"]})
    return segments


def _sidecar_words(sidecar, duration, offset):
    """Read optional TTS word boundaries without making audio generation fragile."""
    if not sidecar.is_file():
        return []
    try:
        # Sidecars are small metadata; reject unexpectedly large files.
        if sidecar.stat().st_size > 2 * 1024 * 1024:
            raise ValueError("Word timing metadata is too large")
        records = json.loads(sidecar.read_text(encoding="utf-8"))
        if not isinstance(records, list):
            raise ValueError("Word timing metadata must be an array")
    except (OSError, UnicodeError, ValueError) as exc:
        log.warning("Ignoring invalid narration timing sidecar %s: %s", sidecar.name, exc)
        return []

    words = []
    for record in records[:2500]:
        if not isinstance(record, dict):
            continue
        word = record.get("word")
        if not isinstance(word, str) or not word.strip():
            continue
        try:
            start, end = float(record["start"]), float(record["end"])
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if not (math.isfinite(start) and math.isfinite(end)):
            continue
        if 0 <= start < end <= duration + 0.5:
            clipped_end = min(end, duration)
            if clipped_end <= start:
                continue
            words.append({
                "word": word[:100],
                "start": round(offset + start, 3),
                "end": round(offset + clipped_end, 3),
            })
    return words


def _run(pid, script, engine):
    run_id = uuid.uuid4().hex
    folder = _root(pid) / run_id
    try:
        parts = _segments(script)
        wavs = []
        durations = []
        for i, segment in enumerate(parts):
            output = folder / ("part_%02d.wav" % i)
            duration = local_narration.synthesize(segment["text"], output, engine)
            wavs.append(output)
            durations.append(duration)
        full = folder / "full.wav"
        actual_duration = local_narration.combine(wavs, full)
        # Timeline comes from the original WAV durations, not assumed word speed.
        offset = 0.0
        timing = []
        exact_words = []
        for i, (segment, duration) in enumerate(zip(parts, durations)):
            sidecar = folder / ("part_%02d.words.json" % i)
            exact_words.extend(_sidecar_words(sidecar, duration, offset))
            timing.append({
                "kind": segment["kind"], "scene_index": segment.get("scene_index"),
                "start": round(offset, 3), "end": round(offset + duration, 3),
                "duration": duration, "text": segment["text"], "file": "part_%02d.wav" % i,
            })
            offset += duration
        artifact = {
            "engine": engine["name"], "voice": engine["voice"],
            "pace": engine.get("pace", 1.0),
            "run_id": run_id, "file": "full.wav",
            "duration": actual_duration, "segments": timing,
            "aligned_words": exact_words, "word_timing_source": "edge" if exact_words else None,
        }
        with db.get_db() as conn:
            conn.execute(
                "UPDATE creator_stages SET status='done', artifact_json=?, error=NULL, "
                "updated_at=CURRENT_TIMESTAMP WHERE project_id=? AND stage='narration'",
                (json.dumps(artifact, ensure_ascii=False), pid))
            conn.execute("UPDATE creator_projects SET status='narration_ready', error=NULL, "
                         "updated_at=CURRENT_TIMESTAMP WHERE id=?", (pid,))
    except Exception as exc:
        log.exception("Narration failed for Creator project %s", pid)
        detail = (str(exc)[:300] if isinstance(exc, local_narration.NarrationError)
                  else "Narration failed; check local logs.")
        with db.get_db() as conn:
            conn.execute("UPDATE creator_stages SET status='failed', error=?, "
                         "updated_at=CURRENT_TIMESTAMP WHERE project_id=? AND stage='narration'",
                         (detail, pid))
            conn.execute("UPDATE creator_projects SET status='script_ready', error=?, "
                         "updated_at=CURRENT_TIMESTAMP WHERE id=?", (detail, pid))


@bp.get("/api/creator/narration/capabilities")
def narration_capabilities():
    options = local_narration.available_backends()
    default = next((e for e in options if e["ready"] and e["name"] == "kokoro"), None)
    if default is None:
        default = next((e for e in options if e["ready"] and not e["online"]), None)
    if default is None:
        default = next((e for e in options if e["ready"]), None)
    return jsonify(engines=options, default=default["name"] if default else None,
                   free=True, online_optional=True)


@bp.post("/api/creator/narration/preview")
def preview_voice():
    """Produce a disposable WAV preview without touching project stages."""
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or set(body) - {"engine", "voice", "pace"}:
        return jsonify(error="Choose an available narration engine and voice."), 400
    if type(body.get("engine")) is not str or type(body.get("voice")) is not str:
        return jsonify(error="Select a voice from the available list."), 400
    try:
        engine = local_narration.with_pace(
            local_narration.select_backend(body["engine"], body["voice"]),
            body.get("pace", 1.0))
    except local_narration.NarrationError as exc:
        return jsonify(error=str(exc)), 400
    if not _preview_lock.acquire(blocking=False):
        return jsonify(error="A voice preview is already running."), 409
    try:
        with tempfile.TemporaryDirectory(prefix="creator-voice-preview-") as folder:
            destination = Path(folder) / "preview.wav"
            local_narration.synthesize(_PREVIEW_TEXT, destination, engine)
            # Keep previews bounded; do not expose arbitrary filesystem paths.
            if not destination.is_file() or destination.stat().st_size > 10 * 1024 * 1024:
                return jsonify(error="Voice preview is unavailable or too large."), 422
            audio = io.BytesIO(destination.read_bytes())
        audio.seek(0)
        return send_file(audio, mimetype="audio/wav", as_attachment=False,
                         download_name="voice-preview.wav", max_age=0)
    except (local_narration.NarrationError, OSError) as exc:
        log.warning("Voice preview failed: %s", type(exc).__name__)
        return jsonify(error="Voice preview failed; check the voice installation."), 503
    finally:
        _preview_lock.release()


@bp.get("/api/creator/projects/<int:pid>/narration")
def narration_status(pid):
    if not _project(pid):
        return jsonify(error="Project not found."), 404
    return jsonify(narration=_stage(pid))


@bp.post("/api/creator/projects/<int:pid>/narration/generate")
def generate_narration(pid):
    body = request.get_json(silent=True)
    if body is None:
        body = {}
    if not isinstance(body, dict):
        return jsonify(error="Invalid voice selection."), 400
    try:
        engine = local_narration.with_pace(
            local_narration.select_backend(body.get("engine"), body.get("voice")),
            body.get("pace", 1.0))
    except local_narration.NarrationError as exc:
        return jsonify(error=str(exc)), 503
    with db.get_db() as conn:
        project = conn.execute("SELECT status, script_json FROM creator_projects "
                               "WHERE id=?", (pid,)).fetchone()
        if not project:
            return jsonify(error="Project not found."), 404
        if project["status"] in ("generating_narration", "generating_script", "rendering", "sourcing_visuals", "aligning_captions", "generating_music", "generating_images"):
            return jsonify(error="Wait until the current generation finishes."), 409
        if not project["script_json"]:
            return jsonify(error="Create or save a script before generating narration."), 409
        script = json.loads(project["script_json"])
        conn.execute("UPDATE creator_projects SET status='generating_narration', error=NULL, "
                     "updated_at=CURRENT_TIMESTAMP WHERE id=?", (pid,))
        conn.execute("UPDATE creator_stages SET status='running', error=NULL, "
                     "updated_at=CURRENT_TIMESTAMP WHERE project_id=? AND stage='narration'", (pid,))
        conn.execute("UPDATE creator_stages SET status='pending', artifact_json=NULL, error=NULL "
                     "WHERE project_id=? AND stage IN ('captions','render')", (pid,))
    _executor.submit(_run, pid, script, engine)
    return jsonify(ok=True, status="generating_narration"), 202


@bp.get("/api/creator/projects/<int:pid>/narration/audio")
def narration_audio(pid):
    if not _project(pid):
        return jsonify(error="Project not found."), 404
    stage = _stage(pid)
    if not stage or stage["status"] != "done" or not stage["artifact"]:
        return jsonify(error="Generate narration first."), 409
    artifact = stage["artifact"]
    index = request.args.get("segment")
    if index is None:
        name = "full.wav"
    else:
        try:
            number = int(index)
        except (TypeError, ValueError):
            return jsonify(error="Invalid segment number."), 400
        if number < 0 or number >= len(artifact["segments"]):
            return jsonify(error="Segment not found."), 404
        name = "part_%02d.wav" % number
    run_id = artifact["run_id"]
    if not isinstance(run_id, str) or len(run_id) != 32 or any(c not in "0123456789abcdef" for c in run_id):
        return jsonify(error="Invalid narration artifact."), 500
    file = _root(pid) / run_id / name
    if not file.is_file():
        return jsonify(error="Narration file is missing."), 404
    return send_file(file, mimetype="audio/wav", as_attachment=False)
