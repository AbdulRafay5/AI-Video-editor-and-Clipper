import io
import json
import logging
import shutil
import uuid
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_file

import config
from clipper import captions as cap
from clipper import db
from clipper import highlights as hl
from clipper import jobs
from clipper import layout as lay
from clipper import llm, video

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[logging.FileHandler(config.LOG_PATH, encoding="utf-8"), logging.StreamHandler()],
)
log = logging.getLogger("clipper.app")

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = config.MAX_UPLOAD_BYTES
db.init_db()


def err(message, code=400):
    return jsonify(error=message), code


def fmt_time(sec):
    sec = int(round(sec))
    h, m, s = sec // 3600, (sec % 3600) // 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def has_active_clips(project_id):
    def count(table):
        row = db.query_one(
            f"SELECT COUNT(*) AS n FROM {table} WHERE project_id=? "
            "AND status IN ('queued','processing')", (project_id,))
        return row["n"]
    return count("clips") + count("transcripts") + count("analyses") > 0


@app.errorhandler(413)
def too_large(_):
    return err(f"File too large (limit {config.MAX_UPLOAD_GB} GB).", 413)


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/status")
def status():
    try:
        nv = video.nvenc_available()
    except video.VideoError as e:
        return jsonify(ffmpeg=False, nvenc=False, message=str(e))
    return jsonify(ffmpeg=True, nvenc=nv,
                   message="Encoding: GPU (NVENC)" if nv else "Encoding: CPU (x264)")


# ---------- projects ----------

@app.get("/api/projects")
def list_projects():
    return jsonify(db.query("SELECT * FROM projects ORDER BY id DESC"))


@app.post("/api/projects")
def upload_project():
    f = request.files.get("video")
    if not f or not f.filename:
        return err("No file selected.")
    ext = Path(f.filename).suffix.lower()
    if ext not in config.ALLOWED_EXTENSIONS:
        return err("Unsupported file type. Use " + ", ".join(sorted(config.ALLOWED_EXTENSIONS)))

    dest = config.UPLOAD_DIR / f"{uuid.uuid4().hex}{ext}"
    try:
        f.save(dest)  # streamed to disk, never held fully in RAM
        info = video.probe(dest)
    except video.VideoError as e:
        dest.unlink(missing_ok=True)
        return err(str(e))
    except Exception:
        dest.unlink(missing_ok=True)
        log.exception("Upload failed")
        return err("Upload failed. See data/clipper.log.", 500)

    pid = db.execute(
        "INSERT INTO projects (name, original_filename, source_path, duration, width, height, size_bytes) "
        "VALUES (?,?,?,?,?,?,?)",
        (Path(f.filename).stem, f.filename, str(dest), info["duration"],
         info["width"], info["height"], dest.stat().st_size))
    return jsonify(db.query_one("SELECT * FROM projects WHERE id=?", (pid,))), 201


@app.delete("/api/projects/<int:pid>")
def delete_project(pid):
    p = db.query_one("SELECT * FROM projects WHERE id=?", (pid,))
    if not p:
        return err("Project not found.", 404)
    if has_active_clips(pid):
        return err("Cancel the running clips, transcription or analysis first.", 409)
    try:
        Path(p["source_path"]).unlink(missing_ok=True)
    except OSError:
        log.exception("Could not delete source file")
    shutil.rmtree(config.OUTPUT_DIR / f"project_{pid}", ignore_errors=True)
    db.execute("DELETE FROM projects WHERE id=?", (pid,))
    return jsonify(ok=True)


# ---------- output format (vertical layouts) ----------

@app.get("/api/projects/<int:pid>/layout")
def get_layout(pid):
    p = db.query_one("SELECT * FROM projects WHERE id=?", (pid,))
    if not p:
        return err("Project not found.", 404)
    layout, pos = lay.normalise(p["layout"], p["crop_pos"])
    w, h = lay.out_size()
    return jsonify(layout=layout, crop_pos=pos, out_width=w, out_height=h)


@app.put("/api/projects/<int:pid>/layout")
def save_layout(pid):
    if not db.query_one("SELECT id FROM projects WHERE id=?", (pid,)):
        return err("Project not found.", 404)
    body = request.get_json(silent=True) or {}
    layout, pos = lay.normalise(body.get("layout"), body.get("crop_pos"))
    db.execute("UPDATE projects SET layout=?, crop_pos=? WHERE id=?", (layout, pos, pid))
    return jsonify(layout=layout, crop_pos=pos)


@app.post("/api/projects/<int:pid>/layout-preview")
def layout_preview(pid):
    p = db.query_one("SELECT * FROM projects WHERE id=?", (pid,))
    if not p:
        return err("Project not found.", 404)
    body = request.get_json(silent=True) or {}
    try:
        t = float(body.get("t", 0))
    except (TypeError, ValueError):
        return err("Invalid preview time.")
    t = min(max(t, 0.0), max(p["duration"] - 0.1, 0.0))
    layout, pos = lay.normalise(body.get("layout"), body.get("crop_pos"))
    caps = style = None
    if body.get("captions"):
        stored = load_captions(pid)
        if stored:
            caps, style = stored["captions"], stored["style"]
    try:
        png, count, note = lay.render_preview(p["source_path"], t, layout, pos, caps, style)
    except video.VideoError as e:
        log.warning("Layout preview failed: %s", e)
        return err(str(e), 500)
    resp = send_file(io.BytesIO(png), mimetype="image/png")
    resp.headers["X-Caption-Events"] = str(count)
    resp.headers["X-Layout-Note"] = (note or "").encode("ascii", "replace").decode("ascii")
    resp.headers["Cache-Control"] = "no-store"
    return resp


# ---------- clips ----------

@app.get("/api/projects/<int:pid>/clips")
def list_clips(pid):
    return jsonify(db.query("SELECT * FROM clips WHERE project_id=? ORDER BY start_time, id", (pid,)))


@app.post("/api/projects/<int:pid>/clips")
def create_clips(pid):
    p = db.query_one("SELECT * FROM projects WHERE id=?", (pid,))
    if not p:
        return err("Project not found.", 404)
    data = request.get_json(silent=True) or {}
    try:
        start = float(data.get("start", 0))
        duration = float(data["duration"])
        count = int(data.get("count", 1))
    except (KeyError, TypeError, ValueError):
        return err("Invalid start time, duration or clip count.")
    burn = bool(data.get("burn_captions"))
    if start < 0 or start >= p["duration"]:
        return err(f"Start time must be between 0 and {fmt_time(p['duration'])}.")
    if not (1 <= duration <= config.MAX_CLIP_SECONDS):
        return err(f"Duration must be 1-{config.MAX_CLIP_SECONDS} seconds.")
    if not (1 <= count <= config.MAX_CLIPS_PER_REQUEST):
        return err(f"Number of clips must be 1-{config.MAX_CLIPS_PER_REQUEST}.")
    if burn and not db.query_one("SELECT project_id FROM captions WHERE project_id=?", (pid,)):
        return err("Generate captions for this project first (section 5), or untick the captions option.")
    layout, pos = lay.normalise(p["layout"], p["crop_pos"])

    created = []
    for i in range(count):
        s = start + i * duration
        e = min(s + duration, p["duration"])
        if e - s < 1:
            break
        cid = db.execute(
            "INSERT INTO clips (project_id, name, start_time, end_time, burn_captions, layout, crop_pos) "
            "VALUES (?,?,?,?,?,?,?)",
            (pid, f"Clip {fmt_time(s)}-{fmt_time(e)}", s, e, 1 if burn else 0, layout, pos))
        jobs.submit_clip(cid)
        created.append(cid)
    if not created:
        return err("Nothing to cut: the start time is too close to the end of the video.")
    return jsonify(clips=created), 201


@app.post("/api/clips/<int:cid>/cancel")
def cancel_clip(cid):
    clip = db.query_one("SELECT * FROM clips WHERE id=?", (cid,))
    if not clip:
        return err("Clip not found.", 404)
    if not jobs.cancel_clip(cid) and clip["status"] in ("queued", "processing"):
        db.execute("UPDATE clips SET status='cancelled' WHERE id=?", (cid,))
    return jsonify(ok=True)


@app.delete("/api/clips/<int:cid>")
def delete_clip(cid):
    clip = db.query_one("SELECT * FROM clips WHERE id=?", (cid,))
    if not clip:
        return err("Clip not found.", 404)
    if clip["status"] in ("queued", "processing"):
        return err("Cancel this clip first.", 409)
    if clip["output_path"]:
        try:
            Path(clip["output_path"]).unlink(missing_ok=True)
        except OSError:
            log.exception("Could not delete clip file")
    db.execute("DELETE FROM clips WHERE id=?", (cid,))
    return jsonify(ok=True)


@app.get("/api/clips/<int:cid>/file")
def clip_file(cid):
    clip = db.query_one("SELECT * FROM clips WHERE id=?", (cid,))
    if not clip or clip["status"] != "done" or not clip["output_path"]:
        return err("Clip file not available.", 404)
    path = Path(clip["output_path"]).resolve()
    if config.OUTPUT_DIR.resolve() not in path.parents or not path.exists():
        return err("Clip file not found on disk.", 404)
    download = request.args.get("download") == "1"
    return send_file(path, mimetype="video/mp4", conditional=True,
                     as_attachment=download, download_name=f"{clip['name']}.mp4")


@app.get("/api/clips/<int:cid>/captions.srt")
def clip_srt(cid):
    clip = db.query_one("SELECT * FROM clips WHERE id=?", (cid,))
    if not clip:
        return err("Clip not found.", 404)
    data = load_captions(clip["project_id"])
    if not data:
        return err("This project has no captions yet.", 404)
    text = cap.to_srt(data["captions"], line_chars=data["style"]["line_chars"],
                      start=clip["start_time"], end=clip["end_time"])
    if not text.strip():
        return err("There is no speech in this clip, so no captions to export.", 404)
    return send_file(io.BytesIO(text.encode("utf-8")), mimetype="application/x-subrip",
                     as_attachment=True, download_name=f"{clip['name']}.srt")


# ---------- transcripts ----------

@app.get("/api/projects/<int:pid>/transcript")
def get_transcript(pid):
    t = db.query_one("SELECT * FROM transcripts WHERE project_id=?", (pid,))
    if not t:
        return jsonify(status="none", progress=0, segments=[])
    segments = []
    if t["status"] == "done" and t["result_json"] and request.args.get("light") != "1":
        data = json.loads(t["result_json"])
        segments = [{"start": s["start"], "end": s["end"], "text": s["text"]}
                    for s in data["segments"]]
    return jsonify(status=t["status"], progress=t["progress"], language=t["language"],
                   device=t["device"], model=t["model"], error=t["error"], segments=segments)


@app.post("/api/projects/<int:pid>/transcribe")
def start_transcription(pid):
    if not db.query_one("SELECT id FROM projects WHERE id=?", (pid,)):
        return err("Project not found.", 404)
    t = db.query_one("SELECT status FROM transcripts WHERE project_id=?", (pid,))
    if t and t["status"] in ("queued", "processing"):
        return err("Transcription is already running.", 409)
    db.execute(
        "INSERT INTO transcripts (project_id, status, model) VALUES (?, 'queued', ?) "
        "ON CONFLICT(project_id) DO UPDATE SET status='queued', progress=0, error=NULL, "
        "result_json=NULL, language=NULL, device=NULL, model=excluded.model",
        (pid, config.WHISPER_MODEL))
    jobs.submit_transcription(pid)
    return jsonify(ok=True), 202


@app.post("/api/projects/<int:pid>/transcribe/cancel")
def cancel_transcription(pid):
    if not jobs.cancel_transcription(pid):
        db.execute("UPDATE transcripts SET status='cancelled' WHERE project_id=? "
                   "AND status IN ('queued','processing')", (pid,))
    return jsonify(ok=True)


# ---------- captions ----------

def load_captions(pid):
    row = db.query_one("SELECT * FROM captions WHERE project_id=?", (pid,))
    if not row:
        return None
    return {"captions": json.loads(row["captions_json"]),
            "style": cap.normalise_style(json.loads(row["style_json"]))}


def public_captions(caps):
    return [{"id": c["id"], "start": c["start"], "end": c["end"], "text": c["text"]} for c in caps]


@app.get("/api/caption-fonts")
def caption_fonts():
    return jsonify(fonts=cap.available_fonts())


@app.get("/api/projects/<int:pid>/captions")
def get_captions(pid):
    if not db.query_one("SELECT id FROM projects WHERE id=?", (pid,)):
        return err("Project not found.", 404)
    data = load_captions(pid)
    if not data:
        return jsonify(exists=False, captions=[], style=cap.normalise_style({}))
    return jsonify(exists=True, captions=public_captions(data["captions"]), style=data["style"])


@app.post("/api/projects/<int:pid>/captions/generate")
def generate_captions(pid):
    if not db.query_one("SELECT id FROM projects WHERE id=?", (pid,)):
        return err("Project not found.", 404)
    t = db.query_one("SELECT status, result_json FROM transcripts WHERE project_id=?", (pid,))
    if not t or t["status"] != "done" or not t["result_json"]:
        return err("Transcribe this video first.")
    body = request.get_json(silent=True) or {}
    try:
        max_words = min(max(int(body.get("max_words", 4)), 1), 12)
    except (TypeError, ValueError):
        return err("Words per caption must be a number.")
    segments = json.loads(t["result_json"])["segments"]
    caps = cap.build_captions(segments, max_words=max_words)
    if not caps:
        return err("The transcript has no timed words to build captions from.")
    existing = db.query_one("SELECT style_json FROM captions WHERE project_id=?", (pid,))
    style_json = existing["style_json"] if existing else json.dumps(cap.normalise_style({}))
    db.execute(
        "INSERT INTO captions (project_id, captions_json, style_json) VALUES (?,?,?) "
        "ON CONFLICT(project_id) DO UPDATE SET captions_json=excluded.captions_json, "
        "updated_at=CURRENT_TIMESTAMP",
        (pid, json.dumps(caps, ensure_ascii=False), style_json))
    return jsonify(count=len(caps)), 201


@app.put("/api/projects/<int:pid>/captions")
def save_captions(pid):
    data = load_captions(pid)
    if not data:
        return err("Generate captions first.", 404)
    body = request.get_json(silent=True) or {}
    if not isinstance(body.get("captions"), list):
        return err("Missing captions list.")
    try:
        new = cap.apply_edits(data["captions"], body["captions"])
    except ValueError as e:
        return err(str(e))
    db.execute("UPDATE captions SET captions_json=?, updated_at=CURRENT_TIMESTAMP WHERE project_id=?",
               (json.dumps(new, ensure_ascii=False), pid))
    return jsonify(captions=public_captions(new))


@app.put("/api/projects/<int:pid>/caption-style")
def save_caption_style(pid):
    if not load_captions(pid):
        return err("Generate captions first.", 404)
    style = cap.normalise_style((request.get_json(silent=True) or {}).get("style"))
    db.execute("UPDATE captions SET style_json=?, updated_at=CURRENT_TIMESTAMP WHERE project_id=?",
               (json.dumps(style), pid))
    return jsonify(style=style)


@app.post("/api/projects/<int:pid>/caption-preview")
def caption_preview(pid):
    p = db.query_one("SELECT * FROM projects WHERE id=?", (pid,))
    if not p:
        return err("Project not found.", 404)
    stored = load_captions(pid)
    if not stored:
        return err("Generate captions first.", 404)
    body = request.get_json(silent=True) or {}
    try:
        t = float(body.get("t", 0))
    except (TypeError, ValueError):
        return err("Invalid preview time.")
    t = min(max(t, 0.0), max(p["duration"] - 0.1, 0.0))
    caps = stored["captions"]
    if isinstance(body.get("captions"), list):
        try:
            caps = cap.apply_edits(caps, body["captions"])
        except ValueError as e:
            return err(str(e))
    style = cap.normalise_style(body.get("style") or stored["style"])
    try:
        info = video.probe(p["source_path"])  # real displayed size (handles rotation)
        png, count = cap.render_preview(p["source_path"], t, caps, style, info["width"], info["height"])
    except video.VideoError as e:
        log.warning("Caption preview failed: %s", e)
        return err(str(e), 500)
    resp = send_file(io.BytesIO(png), mimetype="image/png")
    resp.headers["X-Caption-Events"] = str(count)
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.get("/api/projects/<int:pid>/captions.srt")
def download_srt(pid):
    p = db.query_one("SELECT * FROM projects WHERE id=?", (pid,))
    data = load_captions(pid)
    if not p or not data:
        return err("No captions to export.", 404)
    text = cap.to_srt(data["captions"], line_chars=data["style"]["line_chars"])
    return send_file(io.BytesIO(text.encode("utf-8")), mimetype="application/x-subrip",
                     as_attachment=True, download_name=f"{p['name']}.srt")


# ---------- AI highlights ----------

@app.post("/api/projects/<int:pid>/analyze")
def start_analysis(pid):
    if not db.query_one("SELECT id FROM projects WHERE id=?", (pid,)):
        return err("Project not found.", 404)
    t = db.query_one("SELECT status FROM transcripts WHERE project_id=?", (pid,))
    if not t or t["status"] != "done":
        return err("Transcribe this video first (section 4).")
    a = db.query_one("SELECT status FROM analyses WHERE project_id=?", (pid,))
    if a and a["status"] in ("queued", "processing"):
        return err("Analysis is already running.", 409)
    try:
        settings = hl.normalise_settings(request.get_json(silent=True))
    except ValueError as e:
        return err(str(e))
    st = llm.status()
    if not (st["running"] and st["installed"]):
        return err(st["message"], 503)
    db.execute(
        "INSERT INTO analyses (project_id, status, model, params_json, message) "
        "VALUES (?, 'queued', ?, ?, 'Queued') "
        "ON CONFLICT(project_id) DO UPDATE SET status='queued', progress=0, error=NULL, "
        "message='Queued', model=excluded.model, params_json=excluded.params_json",
        (pid, config.LLM_MODEL, json.dumps(settings)))
    jobs.submit_analysis(pid)
    return jsonify(ok=True), 202


@app.post("/api/projects/<int:pid>/analyze/cancel")
def cancel_analysis(pid):
    if not jobs.cancel_analysis(pid):
        db.execute("UPDATE analyses SET status='cancelled' WHERE project_id=? "
                   "AND status IN ('queued','processing')", (pid,))
    return jsonify(ok=True)


@app.get("/api/projects/<int:pid>/highlights")
def get_highlights(pid):
    a = db.query_one("SELECT * FROM analyses WHERE project_id=?", (pid,))
    rows = db.query("SELECT * FROM highlights WHERE project_id=? ORDER BY score DESC, id", (pid,))
    if not a:
        return jsonify(status="none", progress=0, message=None, error=None, highlights=rows)
    return jsonify(status=a["status"], progress=a["progress"], message=a["message"],
                   error=a["error"], model=a["model"], highlights=rows)


@app.post("/api/highlights/<int:hid>/clip")
def clip_from_highlight(hid):
    h = db.query_one("SELECT * FROM highlights WHERE id=?", (hid,))
    if not h:
        return err("Highlight not found.", 404)
    p = db.query_one("SELECT * FROM projects WHERE id=?", (h["project_id"],))
    burn = bool((request.get_json(silent=True) or {}).get("burn_captions"))
    if burn and not db.query_one("SELECT project_id FROM captions WHERE project_id=?", (h["project_id"],)):
        return err("Generate captions for this project first (section 5), or untick the captions option.")
    layout, pos = lay.normalise(p["layout"], p["crop_pos"])
    name = f"{h['category'] or 'Highlight'} {fmt_time(h['start_time'])}-{fmt_time(h['end_time'])}"
    cid = db.execute(
        "INSERT INTO clips (project_id, name, start_time, end_time, burn_captions, layout, crop_pos) "
        "VALUES (?,?,?,?,?,?,?)",
        (h["project_id"], name, h["start_time"], h["end_time"], 1 if burn else 0, layout, pos))
    jobs.submit_clip(cid)
    return jsonify(clip=cid), 201


if __name__ == "__main__":
    log.info("Open http://127.0.0.1:5000")
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)