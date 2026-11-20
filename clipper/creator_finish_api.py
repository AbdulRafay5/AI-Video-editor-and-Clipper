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
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from flask import Blueprint, jsonify, request, send_file

import config
from . import captions as cap, creator_subtitle_animation as animation, db, local_narration

bp = Blueprint("creator_finish", __name__)
log = logging.getLogger("clipper.creator_finish")
MAX_FADE = 0.8
DEFAULT = {"captions": True, "fade_edges": True, "transition": "cut",
           "animation": "none", "position": "bottom", "highlight_word": False,
           "size_pct": 5.5, "text_color": "#FFFFFF", "highlight_color": "#FFD400"}
TRANSITIONS = {"cut", "fade", "slideleft", "slideright", "zoomin"}
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
    # Edge neural TTS emits real word-boundary events during narration.
    edge_words = artifact.get("aligned_words")
    if artifact.get("word_timing_source") == "edge" and isinstance(edge_words, list) and edge_words:
        clean = []
        for w in edge_words:
            try:
                start, end = float(w["start"]), float(w["end"])
                word = str(w["word"]).strip()
            except (KeyError, ValueError, TypeError):
                continue
            if word and 0 <= start < end:
                clean.append({"word": word[:100], "start": start, "end": end})
        if clean:
            return cap.build_captions([{"words": clean}], max_words=5,
                                      max_chars=32, max_duration=3.5), "edge"
    return timed_captions(artifact), "estimated"


@bp.get("/api/creator/projects/<int:pid>/captions/options")
def get_options(pid):
    if not db.query_one("SELECT id FROM creator_projects WHERE id=?", (pid,)):
        return jsonify(error="Project not found."), 404
    return jsonify(settings=settings(pid), timing="estimated-from-narration-segments")


@bp.put("/api/creator/projects/<int:pid>/captions/options")
def set_options(pid):
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or not {"captions", "fade_edges"} <= set(body) or set(body) - set(DEFAULT):
        return jsonify(error="Choose caption and fade options."), 400
    if any(type(body[k]) is not bool for k in ("captions", "fade_edges")):
        return jsonify(error="Caption and fade selections must be true or false."), 400
    if body.get("transition", "cut") not in TRANSITIONS:
        return jsonify(error="Invalid scene transition."), 400
    if body.get("animation", "none") not in animation.MODES:
        return jsonify(error="Unsupported subtitle animation."), 400
    if body.get("position", "bottom") not in ("bottom", "middle", "top"):
        return jsonify(error="Invalid subtitle placement."), 400
    if type(body.get("highlight_word", False)) is not bool:
        return jsonify(error="Highlight selection must be true or false."), 400
    fontsize = body.get("size_pct", 5.5)
    if (type(fontsize) not in (int, float) or not 2 <= fontsize <= 12):
        return jsonify(error="Subtitle size must be between 2% and 12%."), 400
    for color in ("text_color", "highlight_color"):
        if not isinstance(body.get(color, DEFAULT[color]), str) or not re.fullmatch(
                r"#[0-9A-Fa-f]{6}", body.get(color, DEFAULT[color])):
            return jsonify(error="Subtitle colors must be hexadecimal."), 400
    body = {**DEFAULT, **body}
    with db.get_db() as conn:
        project = conn.execute("SELECT status FROM creator_projects WHERE id=?", (pid,)).fetchone()
        if not project:
            return jsonify(error="Project not found."), 404
        if project["status"] in ("generating_script", "generating_narration",
                                 "rendering", "sourcing_visuals", "aligning_captions", "generating_music", "generating_images"):
            return jsonify(error="Wait until the current stage finishes."), 409
        old = conn.execute("SELECT artifact_json FROM creator_stages "
                           "WHERE project_id=? AND stage='captions'", (pid,)).fetchone()
        previous = json.loads(old["artifact_json"]) if old and old["artifact_json"] else {}
        previous.update(body)
        conn.execute("UPDATE creator_stages SET status=?, artifact_json=?, error=NULL, "
                     "updated_at=CURRENT_TIMESTAMP WHERE project_id=? AND stage='captions'",
                     ("done" if body["captions"] else "pending", json.dumps(previous), pid))
        from . import creator_render_api as render
        render.invalidate_render(conn, pid)
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


def _run_finishing_ffmpeg(cmd, folder, cancel_requested, timeout=900):
    """Stop FFmpeg if the render is cancelled, with bounded cleanup."""
    deadline = time.monotonic() + timeout
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(
            cmd, cwd=folder, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=errors, creationflags=NO_WINDOW)
        try:
            while True:
                if cancel_requested():
                    raise FinishError("Finishing cancelled by user.")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise FinishError("Local subtitle render timed out.")
                try:
                    process.wait(timeout=min(0.2, remaining))
                    break
                except subprocess.TimeoutExpired:
                    continue
            if cancel_requested():
                raise FinishError("Finishing cancelled by user.")
            if process.returncode:
                errors.seek(0, os.SEEK_END)
                end = errors.tell()
                errors.seek(max(0, end - 2048))
                raise FinishError("Could not burn subtitles or fades: " +
                                  errors.read().decode(errors="replace")[-250:])
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()


def finish_project(pid, base_mp4, revision, finished_root, *, cancel_requested=None):
    """Return a safe derived local file path, or the untouched upstream MP4."""
    opts = settings(pid)
    if not opts["captions"] and not opts["fade_edges"] and opts["transition"] == "cut":
        return Path(base_mp4)
    if not isinstance(revision, str) or len(revision) != 32 or any(c not in "0123456789abcdef" for c in revision):
        raise FinishError("Invalid renderer revision.")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise FinishError("FFmpeg is required for finished subtitles and fades.")
    finished_root = Path(finished_root)
    finished_root.mkdir(parents=True, exist_ok=True)
    dest = finished_root / f"finished_{revision}.mp4"
    # Read the actual final canvas so finishing also supports historical MP4
    # inputs and doesn't demand the current revision for the default path.
    probe = shutil.which("ffprobe")
    if not probe:
        raise FinishError("FFprobe is required for accurate subtitle sizing.")
    try:
        dimensions = subprocess.run(
            [probe, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "json",
             str(Path(base_mp4).resolve())],
            capture_output=True, text=True, timeout=30, creationflags=NO_WINDOW)
        info = json.loads(dimensions.stdout)["streams"][0]
        canvas_width, canvas_height = int(info["width"]), int(info["height"])
        if dimensions.returncode or canvas_width <= 0 or canvas_height <= 0:
            raise FinishError("Base video dimensions unavailable.")
    except (OSError, ValueError, KeyError, IndexError, subprocess.TimeoutExpired) as exc:
        raise FinishError("Cannot read base video dimensions.") from exc
    with tempfile.TemporaryDirectory(prefix="creator-finish-", dir=finished_root) as temp:
        folder = Path(temp)
        filters = []
        if opts["captions"]:
            artifact = _narration(pid)
            captions, _ = project_captions(pid)
            style = cap.normalise_style({
                "font": "Arial", "size_pct": opts["size_pct"],
                "position": opts["position"],
                "highlight_word": opts["highlight_word"],
                "text_color": opts["text_color"],
                "highlight_color": opts["highlight_color"],
                "margin_pct": 16.0,
            })
            ass, events = cap.build_ass(captions, style, canvas_width, canvas_height)
            ass = animation.animate_ass(ass, opts["animation"])
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
        cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y"]
        if opts["transition"] != "cut":
            from . import creator_render_api as render
            from .mpt_compat.video_project import stage_graph
            engine = render._engine(pid)
            spec = engine.revision(revision)
            all_stages = {stage.key: stage for stage in stage_graph(spec)}
            scenes = spec["scenes"]
            if len(scenes) < 2:
                raise FinishError("At least two scenes are required for transitions.")
            duration = 0.3
            inputs = []
            for scene in scenes:
                stage = all_stages.get(f"{scene['id']}:scene")
                record = engine._cached(stage) if stage else None
                if not record:
                    raise FinishError("A cached scene render is missing; render again.")
                inputs.append(Path(record["path"]).resolve())
            for item in inputs:
                cmd.extend(["-i", str(item)])
            cmd.extend(["-i", str(Path(base_mp4).resolve())])
            spec_settings = spec["settings"]
            fps = int(spec_settings["fps"])
            width, height = int(spec_settings["width"]), int(spec_settings["height"])
            graph = []
            for index, scene in enumerate(scenes):
                length = float(scene["duration"])
                if length < duration * 2:
                    raise FinishError("Some scenes are too short for a smooth transition.")
                padded = f"tpad=stop_mode=clone:stop_duration={duration:.3f}," if index < len(scenes)-1 else ""
                graph.append(
                    f"[{index}:v]settb=AVTB,setpts=PTS-STARTPTS,"
                    f"scale={width}:{height},fps={fps},format=yuv420p,"
                    + padded + f"trim=duration={length + (duration if index < len(scenes)-1 else 0):.3f},"
                    + f"setpts=PTS-STARTPTS[v{index}]")
            running = float(scenes[0]["duration"])
            prev = "v0"
            for index in range(1, len(scenes)):
                tag = f"x{index}"
                graph.append(f"[{prev}][v{index}]xfade=transition={opts['transition']}:"
                             f"duration={duration:.3f}:offset={running:.3f}[{tag}]")
                prev = tag
                running += float(scenes[index]["duration"])
            if filters:
                graph.append(f"[{prev}]" + ",".join(filters) + "[final]")
            else:
                graph.append(f"[{prev}]null[final]")
            script_file = folder / "filters.txt"
            script_file.write_text(";".join(graph), encoding="utf-8")
            cmd.extend(["-filter_complex_script", str(script_file),
                        "-map", "[final]", "-map", f"{len(scenes)}:a:0"])
        else:
            cmd.extend(["-i", str(Path(base_mp4).resolve()), "-vf", ",".join(filters)])
        cmd.extend(["-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
                    "-c:a", "copy", "-movflags", "+faststart", str(staging)])
        if cancel_requested is not None:
            try:
                _run_finishing_ffmpeg(cmd, folder, cancel_requested)
            except OSError as exc:
                raise FinishError("Local subtitle render failed to start.") from exc
            if cancel_requested():
                raise FinishError("Finishing cancelled by user.")
        else:
            try:
                result = subprocess.run(cmd, cwd=folder, capture_output=True,
                                        text=True, timeout=900, creationflags=NO_WINDOW)
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise FinishError("Local subtitle render failed or timed out.") from exc
            if result.returncode:
                raise FinishError("Could not burn subtitles or fades: " + result.stderr[-250:])
        if not staging.is_file() or staging.stat().st_size < 1024:
            raise FinishError("Finishing did not produce a usable MP4.")
        if cancel_requested is not None and cancel_requested():
            raise FinishError("Finishing cancelled by user.")
        os.replace(staging, dest)
    return dest
