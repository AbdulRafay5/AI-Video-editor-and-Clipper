"""Creator Studio finishing using the existing ASS caption renderer and FFmpeg.

The MoneyPrinterTurbo revision renderer creates the immutable base MP4 first.
This optional *derived* output burns scene captions and adds brief start/end
fades. The source MP4, cached stages and original clipper are never modified.
Narration segment/word timings are estimated proportionally—not Whisper alignment.
"""
import json
import logging
import math
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from flask import Blueprint, jsonify, request, send_file

import config
from . import captions as cap, db, local_narration

bp = Blueprint("creator_finish", __name__)
log = logging.getLogger("clipper.creator_finish")
MAX_FADE = 0.8
DEFAULT = {"captions": True, "fade_edges": True}
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class FinishError(ValueError):
    pass


def settings(pid):
    row = db.query_one(
        "SELECT artifact_json FROM creator_stages WHERE project_id=? AND stage='captions'", (pid,))
    if not row or not row["artifact_json"]:
        return dict(DEFAULT)
    options = json.loads(row["artifact_json"])
    return {key: options.get(key, default) for key, default in DEFAULT.items()}


def timed_captions(artifact):
    """Build honest approximate captions using *segment* boundaries.

    TTS engines like Kokoro do not return word timestamps. Each segment's
    words are distributed proportionally for subtitle visibility only.
    No invented precision is advertised and karaoke highlighting is disabled.
    """
    segments = artifact.get("segments", [])
    if not isinstance(segments, list):
        raise FinishError("Narration timeline is malformed.")
    words = []
    for segment in segments:
        try:
            start, end = float(segment["start"]), float(segment["end"])
            dialogue = str(segment["text"])
        except (ValueError, TypeError, KeyError) as exc:
            raise FinishError("Narration timing missing.") from exc
        if not math.isfinite(start) or not math.isfinite(end) or end <= start:
            raise FinishError("Narration timing is invalid.")
        tokens = dialogue.split()
        if not tokens:
            continue
        for i, word in enumerate(tokens):
            a = start + (end - start) * i / len(tokens)
            b = start + (end - start) * (i+1) / len(tokens)
            words.append({"word": word, "start": round(a, 3), "end": round(b, 3)})
    if not words:
        raise FinishError("Narration has no captionable dialogue.")
    # Caption builder already exists for the original clipper, so reuse it.
    return cap.build_captions([{"words": words}], max_words=5, max_chars=32,
                              max_duration=3.5)


def project_captions(pid):
    """Prefer Whisper-measured word timestamps over estimated TTS word positions."""
    artifact = _narration(pid)
    row = db.query_one("SELECT artifact_json FROM creator_stages "
                       "WHERE project_id=? AND stage='captions'", (pid,))
    data = json.loads(row["artifact_json"]) if row and row["artifact_json"] else {}
    words = data.get("aligned_words")
    if (isinstance(words, list) and words and
            data.get("narration_run_id") == artifact.get("run_id")):
        clean = []
        for w in words:
            if not isinstance(w, dict):
                continue
            try:
                start, end = float(w["start"]), float(w["end"])
            except (KeyError, ValueError, TypeError):
                continue
            if 0 <= start < end and isinstance(w.get("word"), str):
                clean.append({"word": w["word"][:100], "start": start, "end": end})
        if clean:
            return cap.build_captions([{"words": clean}], max_words=5,
                                      max_chars=32, max_duration=3.5), "whisper"
    return timed_captions(artifact), "estimated"


@bp.get("/api/creator/projects/<int:pid>/captions/options")
def get_options(pid):
    if not db.query_one("SELECT id FROM creator_projects WHERE id=?", (pid,)):
        return jsonify(error="Project not found."), 404
    return jsonify(settings=settings(pid), timing="estimated-from-narration-segments")


@bp.put("/api/creator/projects/<int:pid>/captions/options")
def set_options(pid):
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or set(body) != set(DEFAULT):
        return jsonify(error="Choose caption and fade options."), 400
    if any(type(value) is not bool for value in body.values()):
        return jsonify(error="Caption and fade selections must be true or false."), 400
    with db.get_db() as conn:
        project = conn.execute("SELECT status FROM creator_projects WHERE id=?", (pid,)).fetchone()
        if not project:
            return jsonify(error="Project not found."), 404
        if project["status"] in ("generating_script", "generating_narration",
                                 "rendering", "sourcing_visuals", "aligning_captions"):
            return jsonify(error="Wait until the current stage finishes."), 409
        old = conn.execute("SELECT artifact_json FROM creator_stages "
                           "WHERE project_id=? AND stage='captions'", (pid,)).fetchone()
        previous = json.loads(old["artifact_json"]) if old and old["artifact_json"] else {}
        previous.update(body)
        conn.execute("UPDATE creator_stages SET status=?, artifact_json=?, error=NULL, "
                     "updated_at=CURRENT_TIMESTAMP WHERE project_id=? AND stage='captions'",
                     ("done" if body["captions"] else "pending", json.dumps(previous), pid))
        conn.execute("UPDATE creator_stages SET status='pending', artifact_json=NULL, error=NULL "
                     "WHERE project_id=? AND stage='render'", (pid,))
    return jsonify(settings=body)


def _narration(pid):
    row = db.query_one("SELECT status, artifact_json FROM creator_stages "
                       "WHERE project_id=? AND stage='narration'", (pid,))
    if not row or row["status"] != "done" or not row["artifact_json"]:
        raise FinishError("Generate narration first.")
    return json.loads(row["artifact_json"])


@bp.get("/api/creator/projects/<int:pid>/captions.srt")
def download_srt(pid):
    if not db.query_one("SELECT id FROM creator_projects WHERE id=?", (pid,)):
        return jsonify(error="Project not found."), 404
    try:
        captions, _ = project_captions(pid)
        content = cap.to_srt(captions)
    except FinishError as exc:
        return jsonify(error=str(exc)), 409
    from io import BytesIO
    return send_file(BytesIO(content.encode("utf-8")), as_attachment=True,
                     mimetype="application/x-subrip", download_name=f"creator_{pid}.srt")


def finish_project(pid, base_mp4, revision, finished_root):
    """Return a safe derived local file path, or the untouched upstream MP4."""
    opts = settings(pid)
    if not any(opts.values()):
        return Path(base_mp4)
    if not isinstance(revision, str) or len(revision) != 32 or any(c not in "0123456789abcdef" for c in revision):
        raise FinishError("Invalid renderer revision.")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise FinishError("FFmpeg is required for finished subtitles and fades.")
    finished_root = Path(finished_root)
    finished_root.mkdir(parents=True, exist_ok=True)
    dest = finished_root / f"finished_{revision}.mp4"
    with tempfile.TemporaryDirectory(prefix="creator-finish-", dir=finished_root) as temp:
        folder = Path(temp)
        filters = []
        if opts["captions"]:
            artifact = _narration(pid)
            captions, _ = project_captions(pid)
            style = cap.normalise_style({
                "font": "Arial", "size_pct": 5.5, "position": "bottom",
                "highlight_word": False, "margin_pct": 16.0,
            })
            ass, events = cap.build_ass(captions, style, 720, 1280)
            if events == 0:
                raise FinishError("No usable subtitle events.")
            cap.prepare_workdir(folder, ass, style["font"])
            filters.append("subtitles=captions.ass:fontsdir=fonts")
        if opts["fade_edges"]:
            # Only intro and outro fades: scene changes are still hard cuts.
            duration = float(_narration(pid).get("duration") or 0)
            if not 0 < duration < 3600:
                raise FinishError("Narration duration is unavailable.")
            fade = min(MAX_FADE, duration / 4)
            filters.extend([
                f"fade=t=in:st=0:d={fade:.3f}",
                f"fade=t=out:st={max(0, duration-fade):.3f}:d={fade:.3f}",
            ])
        staging = folder / "finished.mp4"
        cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
               "-i", str(Path(base_mp4).resolve()), "-vf", ",".join(filters),
               "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
               "-c:a", "copy", "-movflags", "+faststart", str(staging)]
        try:
            result = subprocess.run(cmd, cwd=folder, capture_output=True,
                                    text=True, timeout=900, creationflags=NO_WINDOW)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise FinishError("Local subtitle render failed or timed out.") from exc
        if result.returncode or not staging.is_file() or staging.stat().st_size < 1024:
            raise FinishError("Could not burn subtitles or fades: " + result.stderr[-250:])
        os.replace(staging, dest)
    return dest
