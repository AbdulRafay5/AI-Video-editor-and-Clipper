"""Non-destructive local B-roll exports. The source clip and audio remain intact."""
import math
import subprocess
import threading
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from flask import Blueprint, jsonify, request, send_file

import config
from . import broll_api, db, video

bp = Blueprint("broll_render", __name__)
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="broll-export")
_lock = threading.Lock()
_jobs = {}
MAX_CUTAWAYS = 6


def validate_plan(raw, project_id, duration):
    """Validate media ownership and clip-relative times before starting FFmpeg."""
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_CUTAWAYS:
        raise ValueError("Select between 1 and 6 B-roll cutaways.")
    planned = []
    for cut in raw:
        if not isinstance(cut, dict) or not isinstance(cut.get("asset_id"), str):
            raise ValueError("Invalid B-roll asset.")
        asset = broll_api._item(project_id, cut["asset_id"])
        if asset is None:
            raise ValueError("B-roll asset is no longer available for this project.")
        try:
            start, end = float(cut["start"]), float(cut["end"])
        except (KeyError, ValueError, TypeError):
            raise ValueError("Each cutaway needs valid start and end times.") from None
        if (not math.isfinite(start) or not math.isfinite(end)
                or start < 0 or end <= start or end > duration - 0.05
                or end - start > 8.0 or end - start < 0.5):
            raise ValueError("B-roll must last 0.5–8 seconds and fit inside the clip.")
        planned.append({"start": round(start, 3), "end": round(end, 3),
                        "asset_id": cut["asset_id"], "path": asset})
    planned.sort(key=lambda x: (x["start"], x["end"]))
    for before, after in zip(planned, planned[1:]):
        if after["start"] < before["end"]:
            raise ValueError("B-roll cutaways must not overlap.")
    return planned


def build_command(source, plan, output, width, height, duration, encoder="x264"):
    """Build one FFmpeg overlay command; media paths are separate subprocess args."""
    args = [video._tool("ffmpeg"), "-hide_banner", "-y", "-i", str(source)]
    for cut in plan:
        if cut["path"].suffix.lower() in broll_api.IMAGE_TYPES:
            args += ["-loop", "1", "-framerate", "30", "-i", str(cut["path"])]
        else:
            args += ["-stream_loop", "-1", "-i", str(cut["path"])]
    graph = []
    prior = "0:v"
    for i, cut in enumerate(plan, start=1):
        overlay = f"asset{i}"
        next_video = f"merged{i}"
        graph.append(f"[{i}:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
                     f"crop={width}:{height},setsar=1,format=yuv420p[{overlay}]")
        graph.append(f"[{prior}][{overlay}]overlay=0:0:eof_action=repeat:"
                     f"enable='gte(t,{cut['start']})*lt(t,{cut['end']})'[{next_video}]")
        prior = next_video
    venc = (["-c:v", "h264_nvenc", "-preset", "p5", "-cq", "21", "-b:v", "0"]
            if encoder == "nvenc" else ["-c:v", "libx264", "-preset", "medium", "-crf", "20"])
    return args + ["-filter_complex", ";".join(graph), "-map", f"[{prior}]",
                   "-map", "0:a:0?", "-t", f"{duration:.3f}", *venc,
                   "-pix_fmt", "yuv420p", "-c:a", "copy", "-movflags", "+faststart",
                   "-progress", "pipe:1", "-nostats", str(output)]


def _render(clip_id, source, plan, destination, duration, width, height):
    temporary = destination.with_name(destination.stem + ".part.mp4")
    try:
        encoder = "nvenc" if video.nvenc_available() else "x264"
        for chosen_encoder in ([encoder, "x264"] if encoder == "nvenc" else [encoder]):
            temporary.unlink(missing_ok=True)
            cmd = build_command(source, plan, temporary, width, height, duration, chosen_encoder)
            tail = deque(maxlen=20)
            process = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                encoding="utf-8", errors="replace", creationflags=video.NO_WINDOW)
            with _lock:
                _jobs[clip_id]["encoder"] = chosen_encoder
            for line in process.stdout:
                line = line.strip()
                if line.startswith(("out_time_us=", "out_time_ms=")):
                    try:
                        progress = max(0., min(0.99, int(line.split("=", 1)[1]) / 1e6 / duration))
                        with _lock:
                            _jobs[clip_id]["progress"] = progress
                    except ValueError:
                        pass
                elif "=" not in line:
                    tail.append(line)
            process.wait()
            if process.returncode == 0:
                break
            if chosen_encoder == "x264":
                raise video.VideoError("B-roll render failed: " + " | ".join(tail)[-350:])
        video.verify_output(temporary, duration, (width, height))
        temporary.replace(destination)
        with _lock:
            _jobs[clip_id].update(status="done", progress=1.0)
    except Exception as exc:
        temporary.unlink(missing_ok=True)
        with _lock:
            _jobs[clip_id].update(status="failed", error=str(exc)[:400])


@bp.post("/api/clips/<int:cid>/broll/render")
def start_broll_export(cid):
    clip = db.query_one("SELECT * FROM clips WHERE id=?", (cid,))
    if not clip or clip["status"] != "done" or not clip["output_path"]:
        return jsonify(error="Finish rendering the original clip first."), 409
    source = Path(clip["output_path"]).resolve()
    if config.OUTPUT_DIR.resolve() not in source.parents or not source.is_file():
        return jsonify(error="The finished clip is missing."), 404
    try:
        info = video.probe(source)
        plan = validate_plan((request.get_json(silent=True) or {}).get("cutaways"),
                             clip["project_id"], info["duration"])
    except (video.VideoError, ValueError) as exc:
        return jsonify(error=str(exc)), 400
    with _lock:
        running = _jobs.get(cid)
        if running and running["status"] in ("queued", "processing"):
            return jsonify(error="A B-roll export is already running for this clip."), 409
        job_id = uuid.uuid4().hex
        out_dir = config.OUTPUT_DIR / f"project_{clip['project_id']}" / "broll"
        out_dir.mkdir(parents=True, exist_ok=True)
        destination = out_dir / f"clip_{cid}_{job_id}.mp4"
        _jobs[cid] = {"id": job_id, "status": "processing", "progress": 0.,
                      "error": None, "encoder": None, "path": str(destination)}
        _executor.submit(_render, cid, source, plan, destination,
                         info["duration"], info["width"], info["height"])
    return jsonify(ok=True, job_id=job_id, status="processing"), 202


@bp.get("/api/clips/<int:cid>/broll/render")
def broll_export_status(cid):
    with _lock:
        state = dict(_jobs.get(cid) or {})
    if not state:
        return jsonify(error="No B-roll export started for this clip."), 404
    state.pop("path", None)
    state["download_url"] = (f"/api/clips/{cid}/broll/render/{state['id']}/file"
                             if state["status"] == "done" else None)
    return jsonify(state)


@bp.get("/api/clips/<int:cid>/broll/render/<job_id>/file")
def download_broll_export(cid, job_id):
    with _lock:
        state = dict(_jobs.get(cid) or {})
    if not state or state["id"] != job_id or state["status"] != "done":
        return jsonify(error="B-roll export is not ready."), 404
    destination = Path(state["path"]).resolve()
    if config.OUTPUT_DIR.resolve() not in destination.parents or not destination.is_file():
        return jsonify(error="Export is no longer available."), 404
    return send_file(destination, mimetype="video/mp4", as_attachment=True,
                     download_name=f"clip_{cid}_with_broll.mp4", conditional=True)
