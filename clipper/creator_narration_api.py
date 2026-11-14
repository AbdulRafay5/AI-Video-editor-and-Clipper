"""Creator Studio narration stage: durable per-scene audio and precise offsets."""
import json
import logging
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from flask import Blueprint, jsonify, request, send_file

import config
from . import db, local_narration

bp = Blueprint("creator_narration", __name__)
log = logging.getLogger("clipper.creator_narration")
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="creator-voice")


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
            if sidecar.is_file():
                raw_words = json.loads(sidecar.read_text(encoding="utf-8"))
                for word in raw_words[:2500]:
                    start, end = float(word["start"]), float(word["end"])
                    if 0 <= start < end <= duration + 0.5:
                        exact_words.append({
                            "word": str(word["word"])[:100],
                            "start": round(offset + start, 3),
                            "end": round(min(offset + end, offset + duration), 3),
                        })
            timing.append({
                "kind": segment["kind"], "scene_index": segment.get("scene_index"),
                "start": round(offset, 3), "end": round(offset + duration, 3),
                "duration": duration, "text": segment["text"], "file": "part_%02d.wav" % i,
            })
            offset += duration
        artifact = {
            "engine": engine["name"], "voice": engine["voice"],
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
        engine = local_narration.select_backend(body.get("engine"), body.get("voice"))
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
