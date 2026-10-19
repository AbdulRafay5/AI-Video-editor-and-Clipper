import json
import logging
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor

import config
from . import captions as cap
from . import db
from . import highlights as hl
from . import llm, transcription, video

log = logging.getLogger("clipper.jobs")

# One heavy job at a time (clip renders, transcriptions and AI analysis share this queue):
# keeps the GPU/CPU from being overloaded on a 6GB laptop GPU.
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="worker")
_cancel_events = {}
_transcribe_cancel = {}
_analysis_cancel = {}
_lock = threading.Lock()


# ---------- clips ----------

def submit_clip(clip_id):
    event = threading.Event()
    with _lock:
        _cancel_events[clip_id] = event
    _executor.submit(_run_clip, clip_id, event)


def cancel_clip(clip_id):
    with _lock:
        event = _cancel_events.get(clip_id)
    if event:
        event.set()
        return True
    return False


def _prepare_captions(clip, clip_id):
    """Build the caption work folder for a clip. Returns (work_dir_or_None, note_or_None)."""
    row = db.query_one("SELECT captions_json, style_json FROM captions WHERE project_id=?",
                       (clip["project_id"],))
    if not row:
        raise video.VideoError("This project has no captions. Generate captions first.")
    caps = json.loads(row["captions_json"])
    style = cap.normalise_style(json.loads(row["style_json"]))
    info = video.probe(clip["source_path"])  # real displayed size (handles rotation)
    ass, count = cap.build_ass(caps, style, info["width"], info["height"],
                               clip["start_time"], clip["end_time"])
    if count == 0:
        return None, "No speech in this part of the video, so no captions were added."
    work_dir = config.WORK_DIR / f"clip_{clip_id}"
    cap.prepare_workdir(work_dir, ass, style["font"])
    return work_dir, None


def _run_clip(clip_id, cancel):
    work_dir = None
    try:
        clip = db.query_one(
            "SELECT c.*, p.source_path FROM clips c "
            "JOIN projects p ON p.id = c.project_id WHERE c.id = ?", (clip_id,))
        if not clip:
            return
        if cancel.is_set():
            db.execute("UPDATE clips SET status='cancelled' WHERE id=?", (clip_id,))
            return

        out_dir = config.OUTPUT_DIR / f"project_{clip['project_id']}"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"clip_{clip_id}.mp4"
        expected = clip["end_time"] - clip["start_time"]

        note = None
        if clip["burn_captions"]:
            work_dir, note = _prepare_captions(clip, clip_id)

        encoder = "nvenc" if video.nvenc_available() else "x264"
        db.execute("UPDATE clips SET status='processing', progress=0, encoder=?, error=NULL, note=? "
                   "WHERE id=?", (encoder, note, clip_id))

        last = {"pct": -1}

        def on_progress(frac):
            pct = int(frac * 100)
            if pct != last["pct"]:
                last["pct"] = pct
                db.execute("UPDATE clips SET progress=? WHERE id=?", (min(frac, 0.99), clip_id))

        def run(enc):
            video.cut_clip(clip["source_path"], clip["start_time"], clip["end_time"],
                           out_path, enc, on_progress, cancel, subtitle_dir=work_dir)

        try:
            run(encoder)
        except video.VideoError as e:
            if encoder != "nvenc":
                raise
            log.warning("NVENC failed (%s); retrying with x264", e)
            encoder = "x264"
            db.execute("UPDATE clips SET encoder=?, progress=0 WHERE id=?", (encoder, clip_id))
            run(encoder)

        video.verify_output(out_path, expected)
        db.execute("UPDATE clips SET status='done', progress=1, output_path=? WHERE id=?",
                   (str(out_path), clip_id))
        log.info("Clip %s done: %s", clip_id, out_path)

    except video.Cancelled:
        db.execute("UPDATE clips SET status='cancelled', progress=0 WHERE id=?", (clip_id,))
    except Exception as e:
        log.exception("Clip %s failed", clip_id)
        msg = str(e) if isinstance(e, video.VideoError) else "Unexpected error. See data/clipper.log."
        db.execute("UPDATE clips SET status='failed', error=? WHERE id=?", (msg, clip_id))
    finally:
        if work_dir:
            shutil.rmtree(work_dir, ignore_errors=True)
        with _lock:
            _cancel_events.pop(clip_id, None)


# ---------- transcription ----------

def submit_transcription(project_id):
    event = threading.Event()
    with _lock:
        _transcribe_cancel[project_id] = event
    _executor.submit(_run_transcription, project_id, event)


def cancel_transcription(project_id):
    with _lock:
        event = _transcribe_cancel.get(project_id)
    if event:
        event.set()
        return True
    return False


def _run_transcription(project_id, cancel):
    try:
        project = db.query_one("SELECT * FROM projects WHERE id=?", (project_id,))
        if not project:
            return
        if cancel.is_set():
            db.execute("UPDATE transcripts SET status='cancelled' WHERE project_id=?", (project_id,))
            return

        db.execute("UPDATE transcripts SET status='processing', progress=0, error=NULL "
                   "WHERE project_id=?", (project_id,))
        last = {"pct": -1}

        def on_progress(frac):
            pct = int(frac * 100)
            if pct != last["pct"]:
                last["pct"] = pct
                db.execute("UPDATE transcripts SET progress=? WHERE project_id=?",
                           (min(frac, 0.99), project_id))

        def on_device(device):
            last["pct"] = -1
            db.execute("UPDATE transcripts SET device=?, progress=0 WHERE project_id=?",
                       (device, project_id))

        result, device = transcription.transcribe_project(project, cancel, on_progress, on_device)
        if not result["segments"]:
            raise video.VideoError("No speech was detected in this video.")

        db.execute(
            "UPDATE transcripts SET status='done', progress=1, device=?, language=?, "
            "result_json=?, error=NULL WHERE project_id=?",
            (device, result.get("language"), json.dumps(result, ensure_ascii=False), project_id))
        log.info("Transcript for project %s done (%s segments, %s)",
                 project_id, len(result["segments"]), device)

    except video.Cancelled:
        db.execute("UPDATE transcripts SET status='cancelled', progress=0 WHERE project_id=?",
                   (project_id,))
    except Exception as e:
        log.exception("Transcription for project %s failed", project_id)
        msg = str(e) if isinstance(e, video.VideoError) else "Unexpected error. See data/clipper.log."
        db.execute("UPDATE transcripts SET status='failed', error=? WHERE project_id=?",
                   (msg, project_id))
    finally:
        with _lock:
            _transcribe_cancel.pop(project_id, None)


# ---------- AI highlight analysis ----------

def submit_analysis(project_id):
    event = threading.Event()
    with _lock:
        _analysis_cancel[project_id] = event
    _executor.submit(_run_analysis, project_id, event)


def cancel_analysis(project_id):
    with _lock:
        event = _analysis_cancel.get(project_id)
    if event:
        event.set()
        return True
    return False


def _run_analysis(project_id, cancel):
    try:
        row = db.query_one("SELECT * FROM analyses WHERE project_id=?", (project_id,))
        project = db.query_one("SELECT * FROM projects WHERE id=?", (project_id,))
        if not row or not project:
            return
        if cancel.is_set():
            db.execute("UPDATE analyses SET status='cancelled' WHERE project_id=?", (project_id,))
            return
        t = db.query_one("SELECT status, result_json FROM transcripts WHERE project_id=?", (project_id,))
        if not t or t["status"] != "done" or not t["result_json"]:
            raise video.VideoError("Transcribe this video first.")

        settings = json.loads(row["params_json"])
        db.execute("UPDATE analyses SET status='processing', progress=0, error=NULL, message=? "
                   "WHERE project_id=?", ("Reading the transcript", project_id))
        sentences = hl.build_sentences(json.loads(t["result_json"])["segments"])

        def on_progress(frac, message):
            db.execute("UPDATE analyses SET progress=?, message=? WHERE project_id=?",
                       (min(frac, 0.99), message, project_id))

        result = hl.analyze(sentences, project["duration"], settings, cancel, on_progress)

        found = result["highlights"]
        db.execute("DELETE FROM highlights WHERE project_id=?", (project_id,))
        for h in found:
            db.execute(
                "INSERT INTO highlights (project_id, start_time, end_time, duration, category, "
                "score, llm_score, reason, note) VALUES (?,?,?,?,?,?,?,?,?)",
                (project_id, h["start_time"], h["end_time"], h["duration"], h["category"],
                 h["score"], h["llm_score"], h["reason"], h["note"]))

        msg = f"Found {len(found)} highlight(s) from {result['chunks']} part(s) of the transcript."
        if result["failed_chunks"]:
            msg += f" {result['failed_chunks']} part(s) could not be analysed."
        if not found:
            msg = "The AI didn't find anything worth clipping with these settings."
        db.execute("UPDATE analyses SET status='done', progress=1, message=? WHERE project_id=?",
                   (msg, project_id))
        log.info("Analysis for project %s: %s", project_id, msg)

    except video.Cancelled:
        db.execute("UPDATE analyses SET status='cancelled', progress=0 WHERE project_id=?", (project_id,))
    except Exception as e:
        log.exception("Analysis for project %s failed", project_id)
        user_facing = isinstance(e, (video.VideoError, llm.LLMError))
        msg = str(e) if user_facing else "Unexpected error. See data/clipper.log."
        db.execute("UPDATE analyses SET status='failed', error=? WHERE project_id=?", (msg, project_id))
    finally:
        llm.unload()  # free the GPU for transcription and rendering
        with _lock:
            _analysis_cancel.pop(project_id, None)