"""Opt-in Creator Studio bridge to MoneyPrinterTurbo's unmodified FFmpeg renderer.

This module does not touch existing clipper projects/routes. Inputs are locally uploaded
video files and narration produced by Creator Studio. No provider or payment calls.
"""
import json
import math
import logging
import os
import shutil
import subprocess
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from flask import Blueprint, jsonify, request, send_file

import config
from . import db, video
from .mpt_compat.video_project import VideoProject, ProjectError, RenderCancelled

bp = Blueprint("creator_render", __name__)
log = logging.getLogger("clipper.creator_render")
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="creator-render")
_upload_lock = threading.RLock()
MAX_FOOTAGE = 250 * 1024 * 1024
VALID_VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv"}
VALID_AUDIO_EXTENSIONS = {".mp3", ".wav", ".flac", ".aac"}
BUSY = ("generating_script", "generating_narration", "rendering", "sourcing_visuals", "aligning_captions", "generating_music", "generating_images", "generating_video")


def _root(pid):
    return config.DATA_DIR / "creator" / str(pid)


def _project(pid):
    return db.query_one("SELECT * FROM creator_projects WHERE id=?", (pid,))


def _slots(script):
    return ["hook"] + [f"scene_{i}" for i in range(len(script["scenes"]))] + ["closing"]


def _visuals(pid):
    row = db.query_one("SELECT artifact_json FROM creator_stages WHERE project_id=? AND stage='visuals'", (pid,))
    if not row or not row["artifact_json"]:
        return {}
    return json.loads(row["artifact_json"]).get("clips", {})


def _narration(pid):
    row = db.query_one("SELECT status, artifact_json FROM creator_stages "
                       "WHERE project_id=? AND stage='narration'", (pid,))
    if not row or row["status"] != "done" or not row["artifact_json"]:
        raise ProjectError("Generate narration before rendering.")
    return json.loads(row["artifact_json"])


def _file_for_slot(pid, filename):
    # Names come from UUIDs generated here, never client-supplied paths.
    if (not isinstance(filename, str) or len(filename) > 80 or
            not filename.startswith("clip_") or
            Path(filename).name != filename):
        raise ProjectError("Invalid media reference.")
    path = _root(pid) / "visuals" / filename
    if not path.is_file():
        raise ProjectError("Missing scene footage. Upload it again.")
    return path


@bp.get("/api/creator/projects/<int:pid>/visuals")
def visuals(pid):
    project = _project(pid)
    if not project:
        return jsonify(error="Project not found."), 404
    script = json.loads(project["script_json"]) if project["script_json"] else None
    row = db.query_one("SELECT artifact_json FROM creator_stages "
                       "WHERE project_id=? AND stage='visuals'", (pid,))
    saved = json.loads(row["artifact_json"]) if row and row["artifact_json"] else {}
    return jsonify(slots=_slots(script) if script else [],
                   clips=saved.get("clips", {}),
                   sources=saved.get("sources", {}))


@bp.get("/api/creator/projects/<int:pid>/visuals/<slot>/preview")
def preview_visual(pid, slot):
    """Stream only the currently assigned project media, with seek support."""
    project = _project(pid)
    if not project:
        return jsonify(error="Project not found."), 404
    if not project["script_json"] or slot not in _slots(json.loads(project["script_json"])):
        return jsonify(error="Unknown scene."), 404
    filename = _visuals(pid).get(slot)
    if not filename:
        return jsonify(error="This scene has no footage yet."), 404
    try:
        source = _file_for_slot(pid, filename)
    except (ProjectError, OSError):
        return jsonify(error="Scene footage is unavailable."), 404
    extension = source.suffix.lower()
    if extension not in VALID_VIDEO_EXTENSIONS:
        return jsonify(error="Unsupported scene footage format."), 415
    mimetype = {".mp4": "video/mp4", ".mov": "video/quicktime",
                ".mkv": "video/x-matroska"}[extension]
    return send_file(source, mimetype=mimetype, conditional=True,
                     as_attachment=False, max_age=0)


@bp.post("/api/creator/projects/<int:pid>/visuals/<slot>")
def upload_visual(pid, slot):
    with _upload_lock:
        project = _project(pid)
        if not project:
            return jsonify(error="Project not found."), 404
        if project["status"] in BUSY:
            return jsonify(error="Wait until the current production stage finishes."), 409
        if not project["script_json"]:
            return jsonify(error="Save a script first."), 409
        script = json.loads(project["script_json"])
        if slot not in _slots(script):
            return jsonify(error="Unknown scene."), 404
        uploaded = request.files.get("video")
        if not uploaded or not uploaded.filename:
            return jsonify(error="Choose a local video."), 400
        suffix = Path(uploaded.filename).suffix.lower()
        if suffix not in VALID_VIDEO_EXTENSIONS:
            return jsonify(error="Upload MP4, MOV or MKV footage."), 400
        upload_dir = _root(pid) / "visuals"
        upload_dir.mkdir(parents=True, exist_ok=True)
        filename = "clip_" + uuid.uuid4().hex + suffix
        target = upload_dir / filename
        try:
            with target.open("wb") as stream:
                total = 0
                while chunk := uploaded.stream.read(1024 * 1024):
                    total += len(chunk)
                    if total > MAX_FOOTAGE:
                        raise ProjectError("Footage exceeds 250MB limit.")
                    stream.write(chunk)
            if total < 1024:
                raise ProjectError("Video file is empty.")
            info = video.probe(target)
            if info["duration"] > 3600:
                raise ProjectError("One footage file must be shorter than an hour.")
            clips = _visuals(pid)
            clips[slot] = filename
            stage = "done" if all(clips.get(s) for s in _slots(script)) else "pending"
            with db.get_db() as conn:
                previous = conn.execute(
                    "SELECT artifact_json FROM creator_stages "
                    "WHERE project_id=? AND stage='visuals'", (pid,)).fetchone()
                artifact = json.loads(previous["artifact_json"]) if previous and previous["artifact_json"] else {}
                sources = artifact.get("sources", {})
                sources.pop(slot, None)  # Manual replacement is not Pixabay footage.
                conn.execute("UPDATE creator_stages SET artifact_json=?, status=?, error=NULL, "
                             "updated_at=CURRENT_TIMESTAMP WHERE project_id=? AND stage='visuals'",
                             (json.dumps({**artifact, "clips": clips, "sources": sources}), stage, pid))
                conn.execute("UPDATE creator_stages SET status='pending', artifact_json=NULL, error=NULL "
                             "WHERE project_id=? AND stage='render'", (pid,))
            # Do not remove files used by older immutable render revisions.
            return jsonify(ok=True, slot=slot, filename=filename,
                           duration=info["duration"], complete=stage=="done"), 201
        except (ProjectError, video.VideoError) as exc:
            target.unlink(missing_ok=True)
            return jsonify(error=str(exc)), 400
        except Exception:
            target.unlink(missing_ok=True)
            log.exception("Visual upload failed")
            return jsonify(error="Could not save scene footage."), 500


def _music(pid):
    row = db.query_one("SELECT status, artifact_json FROM creator_stages "
                       "WHERE project_id=? AND stage='music'", (pid,))
    if row and row["status"] == "done" and row["artifact_json"]:
        artifact = json.loads(row["artifact_json"])
        name = artifact.get("filename")
        if (isinstance(name, str) and name.startswith("music_")
                and Path(name).name == name):
            file = _root(pid) / "music" / name
            if file.is_file():
                return {"filename": name, "file": file, "duration": artifact.get("duration"),
                        "mode": artifact.get("mode", "uploaded")}
    return None


def _probe_audio(file):
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise ProjectError("FFprobe is required to validate audio.")
    try:
        result = subprocess.run(
            [ffprobe, "-v", "error", "-show_format", "-show_streams",
             "-print_format", "json", str(file)],
            capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise ProjectError("Audio file could not be read.")
        data = json.loads(result.stdout)
        if not any(s.get("codec_type") == "audio" for s in data.get("streams", [])):
            raise ProjectError("The uploaded file has no audio track.")
        duration = float(data.get("format", {}).get("duration") or 0)
        if not 0 < duration < 86400:
            raise ProjectError("Music must be shorter than 24 hours.")
        return round(duration, 2)
    except (OSError, subprocess.TimeoutExpired, ValueError, TypeError) as exc:
        raise ProjectError("Could not validate the music file.") from exc


@bp.get("/api/creator/projects/<int:pid>/music")
def music_status(pid):
    if not _project(pid):
        return jsonify(error="Project not found."), 404
    item = _music(pid)
    return jsonify(music=({"filename": item["filename"], "duration": item["duration"],
                          "mode": item["mode"]}
                           if item else None))


@bp.post("/api/creator/projects/<int:pid>/music")
def upload_music(pid):
    with _upload_lock:
        project = _project(pid)
        if not project:
            return jsonify(error="Project not found."), 404
        if project["status"] in BUSY:
            return jsonify(error="Wait until the current production stage finishes."), 409
        uploaded = request.files.get("audio")
        if not uploaded or not uploaded.filename:
            return jsonify(error="Select a local music file."), 400
        suffix = Path(uploaded.filename).suffix.lower()
        if suffix not in VALID_AUDIO_EXTENSIONS:
            return jsonify(error="Upload MP3, WAV, FLAC or AAC audio."), 400
        folder = _root(pid) / "music"
        folder.mkdir(parents=True, exist_ok=True)
        name = "music_" + uuid.uuid4().hex + suffix
        file = folder / name
        try:
            length = 0
            with file.open("wb") as dst:
                while chunk := uploaded.stream.read(1024 * 1024):
                    length += len(chunk)
                    if length > 30 * 1024 * 1024:
                        raise ProjectError("Music exceeds the 30MB limit.")
                    dst.write(chunk)
            if length < 128:
                raise ProjectError("Audio file is empty.")
            duration = _probe_audio(file)
            with db.get_db() as conn:
                conn.execute("UPDATE creator_stages SET status='done', artifact_json=?, error=NULL, "
                             "updated_at=CURRENT_TIMESTAMP WHERE project_id=? AND stage='music'",
                             (json.dumps({"filename": name, "duration": duration}), pid))
                conn.execute("UPDATE creator_stages SET status='pending', artifact_json=NULL, error=NULL "
                             "WHERE project_id=? AND stage='render'", (pid,))
            return jsonify(ok=True, duration=duration), 201
        except ProjectError as exc:
            file.unlink(missing_ok=True)
            return jsonify(error=str(exc)), 400
        except Exception:
            file.unlink(missing_ok=True)
            log.exception("Music upload failed")
            return jsonify(error="Music upload failed."), 500


@bp.delete("/api/creator/projects/<int:pid>/music")
def clear_music(pid):
    with _upload_lock:
        project = _project(pid)
        if not project:
            return jsonify(error="Project not found."), 404
        if project["status"] in BUSY:
            return jsonify(error="Wait for current generation."), 409
        with db.get_db() as conn:
            conn.execute("UPDATE creator_stages SET status='pending', artifact_json=NULL, error=NULL "
                         "WHERE project_id=? AND stage='music'", (pid,))
            conn.execute("UPDATE creator_stages SET status='pending', artifact_json=NULL, error=NULL "
                         "WHERE project_id=? AND stage='render'", (pid,))
        return jsonify(ok=True)


def _manifest(pid, project):
    script = json.loads(project["script_json"])
    clips = _visuals(pid)
    slots = _slots(script)
    if not all(slot in clips for slot in slots):
        raise ProjectError("Upload a video for the hook, every scene and the closing.")
    artifact = _narration(pid)
    if len(artifact["segments"]) != len(slots):
        raise ProjectError("Scene count changed after narration. Regenerate narration.")
    from . import creator_edit_api as editor
    edit = editor.options(pid)
    mapped = editor.footage_mapping(slots, pid, edit["order"])
    # Manual choices override the global reverse/shuffle strategy per slot.
    # A source is referenced by an existing slot, never a caller path.
    mapped.update(editor.scene_assignments(pid, slots))
    effects = editor.scene_effects(pid, slots)
    width, height = editor.FORMATS[edit["format"]]
    scenes = []
    for index, slot in enumerate(slots):
        segment = artifact["segments"][index]
        run_id = artifact["run_id"]
        if (not isinstance(run_id, str) or len(run_id) != 32 or
                any(c not in "0123456789abcdef" for c in run_id)):
            raise ProjectError("Invalid narration artifact.")
        audio = _root(pid) / "narration" / run_id / segment["file"]
        if not audio.is_file():
            raise ProjectError("Narration audio missing; regenerate it.")
        scenes.append({
            "id": slot,
            "footage": str(editor.prepare_footage(
                pid, _file_for_slot(pid, clips[mapped[slot]]), slot,
                {**edit, **effects.get(slot, {})})),
            "audio": str(audio),
            "narration": segment["text"],
            "duration": float(segment["duration"]),
        })
    music = _music(pid)
    return {
        "project_id": f"creator_{pid}", "scenes": scenes,
        "bgm": str(music["file"]) if music else None,
        "settings": {"width": width, "height": height, "fps": 30,
                     "narration_volume": edit["narration_volume"],
                     "bgm_volume": edit["bgm_volume"],
                     "music_ducking": edit["music_ducking"]},
    }


def _engine(pid):
    return VideoProject(_root(pid) / "mpt_render")


def _verify_export(path, expected_seconds=None):
    """Probe a completed export before announcing it as ready for review."""
    path = Path(path)
    if not path.is_file() or path.stat().st_size < 1024:
        raise ProjectError("Final MP4 is missing or empty.")
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise ProjectError("FFprobe is required to verify the finished MP4.")
    try:
        result = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries",
             "format=duration:stream=codec_type,codec_name,width,height",
             "-of", "json", str(path)],
            capture_output=True, text=True, timeout=45,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode:
            raise ProjectError("Finished MP4 could not be decoded.")
        data = json.loads(result.stdout)
        streams = data.get("streams")
        if not isinstance(streams, list):
            raise ValueError("No media streams")
        video_streams = [stream for stream in streams
                         if isinstance(stream, dict) and stream.get("codec_type") == "video"]
        audio_streams = [stream for stream in streams
                         if isinstance(stream, dict) and stream.get("codec_type") == "audio"]
        if (not video_streams or not audio_streams
                or any(type(s.get("width")) is not int or s["width"] < 1
                       or type(s.get("height")) is not int or s["height"] < 1
                       for s in video_streams)):
            raise ValueError("Missing audio or usable video")
        duration = float(data["format"]["duration"])
        if not math.isfinite(duration) or not 0.1 < duration <= 3600:
            raise ValueError("Invalid output duration")
        if expected_seconds is not None:
            expected = float(expected_seconds)
            if not math.isfinite(expected) or expected <= 0:
                raise ValueError("Invalid expected duration")
            if abs(duration - expected) > max(1.0, expected * 0.10):
                raise ValueError("Output duration differs from narration")
        return {"duration": round(duration, 3),
                "bytes": path.stat().st_size,
                "width": video_streams[0]["width"],
                "height": video_streams[0]["height"],
                "has_audio": True}
    except (OSError, subprocess.TimeoutExpired, KeyError, TypeError, ValueError,
            json.JSONDecodeError) as exc:
        raise ProjectError("Finished MP4 failed audio/video validation.") from exc


def _run(pid, revision):
    try:
        result = _engine(pid).render(revision)
        from . import creator_finish_api
        # The upstream cached render remains immutable. Finishing writes a
        # separate MP4 and never touches existing clipper outputs.
        base = Path(result["export"]["path"])
        finished = creator_finish_api.finish_project(
            pid, base, revision, _root(pid) / "finished")
        # Do not promote a corrupt or unexpectedly short MP4 to review.
        narration = _narration(pid)
        expected = sum(float(part["duration"]) for part in narration["segments"])
        verified = _verify_export(finished, expected)
        safe = {
            "verification": verified,
            "revision_id": revision, "state": result["state"],
            "promoted": result.get("promoted", False),
            "reused_stages": sum(v.get("reused", False) for v in result.get("stages", {}).values()),
            "finished": finished != base,
            "final_name": finished.name if finished != base else None,
        }
        with db.get_db() as conn:
            conn.execute("UPDATE creator_projects SET status='render_ready', error=NULL, "
                         "updated_at=CURRENT_TIMESTAMP WHERE id=?", (pid,))
            conn.execute("UPDATE creator_stages SET status='done', artifact_json=?, error=NULL, "
                         "updated_at=CURRENT_TIMESTAMP WHERE project_id=? AND stage='render'",
                         (json.dumps(safe), pid))
    except BaseException as exc:
        log.exception("Creator render failed for %s", pid)
        detail = str(exc)[:300] if isinstance(exc, (ProjectError, RenderCancelled)) else "Video rendering failed; check the local log."
        with db.get_db() as conn:
            conn.execute("UPDATE creator_projects SET status='narration_ready', error=?, "
                         "updated_at=CURRENT_TIMESTAMP WHERE id=?", (detail, pid))
            conn.execute("UPDATE creator_stages SET status='failed', error=?, "
                         "updated_at=CURRENT_TIMESTAMP WHERE project_id=? AND stage='render'",
                         (detail, pid))


@bp.post("/api/creator/projects/<int:pid>/render")
def start_render(pid):
    with db.get_db() as conn:
        project = conn.execute("SELECT * FROM creator_projects WHERE id=?", (pid,)).fetchone()
        if not project:
            return jsonify(error="Project not found."), 404
        if project["status"] in BUSY:
            return jsonify(error="Wait for the active stage to finish."), 409
        if not project["script_json"]:
            return jsonify(error="Create a script first."), 409
        try:
            manifest = _manifest(pid, project)
            engine = _engine(pid)
            spec = engine.create_revision(manifest)
        except (ProjectError, OSError, ValueError, video.VideoError) as exc:
            return jsonify(error=str(exc)[:350]), 400
        conn.execute("UPDATE creator_projects SET status='rendering', error=NULL, "
                     "updated_at=CURRENT_TIMESTAMP WHERE id=?", (pid,))
        conn.execute("UPDATE creator_stages SET status='running', error=NULL, "
                     "artifact_json=?, updated_at=CURRENT_TIMESTAMP WHERE project_id=? AND stage='render'",
                     (json.dumps({"revision_id": spec["revision_id"]}), pid))
    _executor.submit(_run, pid, spec["revision_id"])
    return jsonify(ok=True, revision_id=spec["revision_id"], status="rendering"), 202


def _finished_export(pid, artifact):
    """Resolve only a server-generated MP4 inside the project's export folders."""
    revision = artifact.get("revision_id")
    final_name = artifact.get("final_name")
    if final_name is not None:
        if (not isinstance(revision, str) or len(revision) != 32
                or any(c not in "0123456789abcdef" for c in revision)
                or final_name != f"finished_{revision}.mp4"):
            raise ProjectError("Invalid final export artifact.")
        parent = (_root(pid) / "finished").resolve()
        finished = (parent / final_name).resolve()
    else:
        info = _engine(pid).metadata().get("last_successful_export")
        if (not isinstance(info, dict) or not isinstance(revision, str)
                or info.get("revision_id") != revision):
            raise ProjectError("Saved render revision does not match the latest export.")
        parent = (_root(pid) / "mpt_render" / "artifacts").resolve()
        finished = Path(info["path"]).resolve()
    if (parent not in finished.parents or finished.suffix.lower() != ".mp4"
            or not finished.is_file() or finished.stat().st_size < 1024):
        raise ProjectError("Final MP4 is missing or empty.")
    verified = artifact.get("verification")
    if isinstance(verified, dict):
        if type(verified.get("bytes")) is not int or finished.stat().st_size != verified["bytes"]:
            raise ProjectError("Final MP4 changed after verification.")
    return finished


@bp.get("/api/creator/projects/<int:pid>/render")
def render_status(pid):
    project = _project(pid)
    if not project:
        return jsonify(error="Project not found."), 404
    row = db.query_one("SELECT status, artifact_json, error FROM creator_stages "
                       "WHERE project_id=? AND stage='render'", (pid,))
    state = row["status"] if row else "pending"
    artifact = json.loads(row["artifact_json"]) if row and row["artifact_json"] else {}
    revision = artifact.get("revision_id")
    stages = {}
    if revision:
        try:
            live = _engine(pid).status(revision)
            stages = live.get("stages", {})
        except (ProjectError, OSError, ValueError, KeyError):
            pass
    available = False
    if state == "done":
        try:
            available = bool(_finished_export(pid, artifact))
        except (ProjectError, OSError, KeyError, TypeError, ValueError):
            pass
    return jsonify(status=state, error=row["error"] if row else None,
                   revision_id=revision, completed_stages=len(stages),
                   verification=artifact.get("verification") if available else None,
                   ready=available, download_url=(
                       f"/api/creator/projects/{pid}/render/file" if available else None),
                   preview_url=(
                       f"/api/creator/projects/{pid}/render/file?preview=1"
                       if available else None))


@bp.post("/api/creator/projects/<int:pid>/render/cancel")
def cancel_render(pid):
    project = _project(pid)
    if not project:
        return jsonify(error="Project not found."), 404
    if project["status"] != "rendering":
        return jsonify(error="No active render to cancel."), 409
    row = db.query_one("SELECT artifact_json FROM creator_stages "
                       "WHERE project_id=? AND stage='render'", (pid,))
    artifact = json.loads(row["artifact_json"]) if row and row["artifact_json"] else {}
    revision = artifact.get("revision_id")
    if not isinstance(revision, str):
        return jsonify(error="Active revision could not be identified."), 409
    try:
        _engine(pid).cancel(revision)
    except (ProjectError, OSError, ValueError):
        return jsonify(error="Could not cancel the active render."), 500
    return jsonify(ok=True, status="cancellation_requested")


@bp.get("/api/creator/projects/<int:pid>/render/file")
def render_file(pid):
    if not _project(pid):
        return jsonify(error="Project not found."), 404
    row = db.query_one("SELECT status, artifact_json FROM creator_stages "
                       "WHERE project_id=? AND stage='render'", (pid,))
    if not row or row["status"] != "done":
        return jsonify(error="Render a video first."), 409
    try:
        artifact = json.loads(row["artifact_json"]) if row["artifact_json"] else {}
        file = _finished_export(pid, artifact)
        # Streaming inline allows browser playback, seeking and range requests;
        # the default still returns an attachment for intentional downloads.
        inline = request.args.get("preview") == "1"
        return send_file(file, mimetype="video/mp4", conditional=True,
                         as_attachment=not inline,
                         download_name=f"creator_{pid}.mp4")
    except (ProjectError, OSError, KeyError, TypeError, ValueError):
        return jsonify(error="The final MP4 is unavailable. Re-render this project."), 404
