"""Opt-in neural background music, following the Creator Studio project lifecycle.

MusicGen Small provides real local prompt-conditioned neural audio. No paid
provider or remote inference API is used. Model weights are downloaded only
when explicitly approved; old music is retained on any failure.
"""
import json
import logging
import uuid
from concurrent.futures import ThreadPoolExecutor

from flask import Blueprint, jsonify, request, send_file

from . import creator_render_api as creator, db, local_musicgen as musicgen, creator_inference

bp = Blueprint("creator_neural_music", __name__)
log = logging.getLogger("clipper.creator_neural_music")
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="creator-musicgen")
MOODS = ("cinematic", "calm", "uplifting", "suspenseful", "energetic", "ambient")


def _narration(pid):
    row = db.query_one(
        "SELECT status, artifact_json FROM creator_stages WHERE project_id=? AND stage='narration'", (pid,))
    if row and row["status"] == "done" and row["artifact_json"]:
        return json.loads(row["artifact_json"])
    return None


def _request(pid, data):
    project = creator._project(pid)
    if not project:
        raise LookupError("Project not found.")
    if not isinstance(data, dict):
        raise ValueError("Expected music mood and prompt options.")
    if set(data) - {"mood", "prompt", "duration", "download_model", "sample_seconds"}:
        raise ValueError("Unknown music generation option.")
    mood = data.get("mood", "cinematic")
    if mood not in MOODS:
        raise ValueError("Choose a supported soundtrack mood.")
    prompt = data.get("prompt", "")
    if type(prompt) is not str or len(prompt) > musicgen.MAX_PROMPT:
        raise ValueError("Music prompt must be 500 characters or fewer.")
    duration = data.get("duration", "match")
    if duration != "match" and (
        type(duration) is not int or not musicgen.MIN_TARGET <= duration <= musicgen.MAX_SECONDS
    ):
        raise ValueError("Duration must be 'match' or 6–180 seconds.")
    sample_seconds = data.get("sample_seconds", musicgen.SAMPLE_SECONDS)
    if type(sample_seconds) is not int or sample_seconds not in (8, 12, 16, 20):
        raise ValueError("Neural composition length must be 8, 12, 16 or 20 seconds.")
    allow_download = data.get("download_model", False)
    if type(allow_download) is not bool:
        raise ValueError("Download permission must be true or false.")
    script = json.loads(project["script_json"]) if project["script_json"] else {}
    target = (musicgen.soundtrack_seconds(project, _narration(pid))
              if duration == "match" else duration)
    prepared_prompt = musicgen.build_prompt(project["topic"], script, mood, prompt)
    return {"mood": mood, "prompt": prepared_prompt, "seconds": target,
            "allow_download": allow_download, "sample_seconds": sample_seconds}


@bp.get("/api/creator/music/neural/capabilities")
def capabilities():
    return jsonify(model=musicgen.MODEL_ID, installed=musicgen.dependencies_ready(),
                   moods=MOODS, free=True, local_inference=True,
                   requires_first_download=True, max_duration=musicgen.MAX_SECONDS,
                   note="Generates a short neural sample and crossfades repetitions to match video length.")


@bp.post("/api/creator/projects/<int:pid>/music/neural/preview")
def preview(pid):
    try:
        options = _request(pid, request.get_json(silent=True))
    except LookupError:
        return jsonify(error="Project not found."), 404
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    return jsonify(prompt=options["prompt"], duration=options["seconds"],
                   sample_seconds=options["sample_seconds"],
                   model=musicgen.MODEL_ID, uses_narration_duration=True)


@bp.get("/api/creator/projects/<int:pid>/music/neural")
def status(pid):
    if not creator._project(pid):
        return jsonify(error="Project not found."), 404
    row = db.query_one("SELECT status, report_json FROM creator_music_jobs "
                       "WHERE project_id=?", (pid,))
    report = json.loads(row["report_json"]) if row and row["report_json"] else {}
    return jsonify(status=row["status"] if row else "pending", report=report)


@bp.get("/api/creator/projects/<int:pid>/music/neural/audio")
def listen(pid):
    if not creator._project(pid):
        return jsonify(error="Project not found."), 404
    row = db.query_one("SELECT status, artifact_json FROM creator_stages "
                       "WHERE project_id=? AND stage='music'", (pid,))
    if not row or row["status"] != "done" or not row["artifact_json"]:
        return jsonify(error="Generate neural music first."), 409
    saved = json.loads(row["artifact_json"])
    if saved.get("mode") != "musicgen":
        return jsonify(error="The selected music is not a MusicGen track."), 409
    source = creator._music(pid)
    if not source or source["file"].suffix.lower() != ".wav":
        return jsonify(error="Selected neural music is missing."), 404
    return send_file(source["file"], mimetype="audio/wav", as_attachment=False)


def _generate(pid, options):
    name = "music_" + uuid.uuid4().hex + ".wav"
    output = creator._root(pid) / "music" / name
    try:
        details = musicgen.generate(
            output, options["prompt"], options["seconds"],
            allow_download=options["allow_download"],
            sample_seconds=options.get("sample_seconds", musicgen.SAMPLE_SECONDS))
        validated_duration = creator._probe_audio(output)
        if abs(validated_duration - options["seconds"]) > 0.5:
            raise musicgen.MusicGenError("Generated soundtrack duration did not match the video.")
        with creator._upload_lock:
            with db.get_db() as conn:
                # Change both stage and project state together. On failures, the
                # existing soundtrack remains selected and render-ready.
                conn.execute(
                    "UPDATE creator_stages SET status='done', error=NULL, artifact_json=?, "
                    "updated_at=CURRENT_TIMESTAMP WHERE project_id=? AND stage='music'",
                    (json.dumps({
                        "filename": name, "duration": validated_duration,
                        "mode": "musicgen", "prompt": options["prompt"],
                        "mood": options["mood"], "model": musicgen.MODEL_ID,
                        "sample_seconds": details["sample_seconds"],
                    }), pid))
                conn.execute(
                    "UPDATE creator_stages SET status='pending', error=NULL, artifact_json=NULL "
                    "WHERE project_id=? AND stage='render'", (pid,))
                conn.execute(
                    "UPDATE creator_music_jobs SET status='done', report_json=?, "
                    "updated_at=CURRENT_TIMESTAMP WHERE project_id=?",
                    (json.dumps({
                        "model": musicgen.MODEL_ID, "duration": validated_duration,
                        "sample_seconds": details["sample_seconds"],
                        "looped": details["looped"],
                        "device": details["device"],
                        "prompt": options["prompt"],
                    }), pid))
                conn.execute("UPDATE creator_projects SET status=?, error=NULL, "
                             "updated_at=CURRENT_TIMESTAMP WHERE id=?",
                             (_ready_state(conn, pid), pid))
    except Exception as exc:
        output.unlink(missing_ok=True)
        log.exception("MusicGen failed for Creator project %s", pid)
        # Surface a useful, bounded diagnostic in the local UI instead of
        # hiding every unexpected exception behind a generic MusicGen failure.
        # Full tracebacks remain in the private application log.
        if isinstance(exc, musicgen.MusicGenError):
            message = str(exc)[:260]
        elif isinstance(exc, (RuntimeError, ImportError, OSError, ValueError, MemoryError)):
            message = ("MusicGen " + type(exc).__name__ +
                       ": " + str(exc).replace("\\n", " ")[:190])[:260]
        else:
            message = "MusicGen failed unexpectedly. Check the local application log."
        with creator._upload_lock:
            with db.get_db() as conn:
                conn.execute(
                    "UPDATE creator_music_jobs SET status='failed', report_json=?, "
                    "updated_at=CURRENT_TIMESTAMP WHERE project_id=?",
                    (json.dumps({"error": message}), pid))
                conn.execute(
                    "UPDATE creator_projects SET status=?, error=?, "
                    "updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (_ready_state(conn, pid), message, pid))


def _ready_state(conn, pid):
    row = conn.execute(
        "SELECT status FROM creator_stages WHERE project_id=? AND stage='narration'",
        (pid,)).fetchone()
    if row and row["status"] == "done":
        return "narration_ready"
    row = conn.execute("SELECT script_json FROM creator_projects WHERE id=?", (pid,)).fetchone()
    return "script_ready" if row and row["script_json"] else "draft"


@bp.post("/api/creator/projects/<int:pid>/music/neural/generate")
def generate(pid):
    data = request.get_json(silent=True)
    try:
        options = _request(pid, data)
    except LookupError:
        return jsonify(error="Project not found."), 404
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    if not musicgen.dependencies_ready():
        return jsonify(error="Install the optional local MusicGen dependencies first.",
                       requirements="requirements-music.txt"), 503
    with creator._upload_lock:
        with db.get_db() as conn:
            project = conn.execute(
                "SELECT status FROM creator_projects WHERE id=?", (pid,)).fetchone()
            if not project:
                return jsonify(error="Project not found."), 404
            if project["status"] in creator.BUSY:
                return jsonify(error="Another production stage is active."), 409
            conn.execute(
                "UPDATE creator_projects SET status='generating_music', error=NULL, "
                "updated_at=CURRENT_TIMESTAMP WHERE id=?", (pid,))
            conn.execute(
                "INSERT INTO creator_music_jobs(project_id,status,report_json) "
                "VALUES (?,'running',?) ON CONFLICT(project_id) DO UPDATE SET "
                "status='running', report_json=excluded.report_json, updated_at=CURRENT_TIMESTAMP",
                (pid, json.dumps({"model": musicgen.MODEL_ID, "prompt": options["prompt"],
                                  "duration": options["seconds"],
                                  "sample_seconds": options["sample_seconds"]})))
    try:
        _executor.submit(creator_inference.run_exclusive, _generate, pid, options)
    except RuntimeError:
        with creator._upload_lock:
            with db.get_db() as conn:
                conn.execute(
                    "UPDATE creator_projects SET status=?,error=?,"
                    "updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='generating_music'",
                    (_ready_state(conn, pid),
                     "Local music worker unavailable; restart the application.", pid))
                conn.execute(
                    "UPDATE creator_music_jobs SET status='failed',report_json=?,"
                    "updated_at=CURRENT_TIMESTAMP WHERE project_id=?",
                    (json.dumps({"error":
                        "Local music worker unavailable; restart the application."}), pid))
        return jsonify(error="Local music worker unavailable; restart the application."), 503
    return jsonify(ok=True, status="generating_music", model=musicgen.MODEL_ID,
                   duration=options["seconds"]), 202
