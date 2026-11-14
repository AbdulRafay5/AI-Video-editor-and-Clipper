"""Opt-in automatic Creator Studio production pipeline.

Reuses existing Flask stage routes, workers and validation; does not duplicate
Ollama/TTS/stock/FFmpeg logic or publish videos without approval.
"""
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from flask import Blueprint, jsonify, request

from . import creator_render_api as render, db

bp = Blueprint("creator_auto", __name__)
log = logging.getLogger("clipper.creator_auto")
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="creator-auto")
_lock = threading.RLock()
STEPS = ("script", "narration", "footage", "music", "captions", "render", "review")
WAIT_SECONDS = {"script": 420, "narration": 1200, "footage": 1800,
                "music": 1800, "captions": 900, "render": 1800}
MAX_BATCHES = 5


class AutoError(ValueError):
    pass


def validate(payload):
    if not isinstance(payload, dict):
        raise AutoError("Select automation options.")
    expected = {"approve_stock_download", "voice_engine", "voice", "music", "align_captions", "approve_model_download", "use_ai_images", "approve_image_model_download", "visual_mode"}
    if set(payload) - expected:
        raise AutoError("Unexpected automation option.")
    stock = payload.get("approve_stock_download", False)
    aligned = payload.get("align_captions", False)
    music_download = payload.get("approve_model_download", False)
    images = payload.get("use_ai_images", False)
    image_download = payload.get("approve_image_model_download", False)
    if type(stock) is not bool or type(aligned) is not bool or type(music_download) is not bool or type(images) is not bool or type(image_download) is not bool:
        raise AutoError("Download approval and alignment must be true or false.")
    mode = payload.get("visual_mode")
    if mode is not None and mode not in ("stock", "ai", "neural_video"):
        raise AutoError("Visual source must be stock, AI images or true neural video.")
    if mode == "neural_video":
        if stock or images:
            raise AutoError("Neural video mode cannot use stock downloads or image animation.")
    if mode == "ai":
        if stock:
            raise AutoError("AI visual mode does not allow stock footage downloads.")
        images = True
    if mode == "stock" and images:
        raise AutoError("Stock mode cannot also request AI image fallback.")
    voice_engine = payload.get("voice_engine")
    voice = payload.get("voice")
    if voice_engine is not None and (not isinstance(voice_engine, str) or len(voice_engine) > 40):
        raise AutoError("Invalid voice engine.")
    if voice is not None and (not isinstance(voice, str) or len(voice) > 80):
        raise AutoError("Invalid voice.")
    music = payload.get("music", "keep")
    if music not in ("keep", "instrumental", "neural", "none"):
        raise AutoError("Choose existing music, local instrumental or none.")
    return {"approve_stock_download": stock, "voice_engine": voice_engine,
            "voice": voice, "music": music, "align_captions": aligned,
            "approve_model_download": music_download,
            "use_ai_images": images, "approve_image_model_download": image_download,
            "visual_mode": mode or "legacy"}


def status(pid):
    row = db.query_one("SELECT * FROM creator_auto_jobs WHERE project_id=?", (pid,))
    if not row:
        return {"status": "pending", "step": "idle", "report": {}}
    return {"status": row["status"], "step": row["step"],
            "report": json.loads(row["report_json"] or "{}")}


def _save(pid, state, step, report):
    with db.get_db() as conn:
        conn.execute("UPDATE creator_auto_jobs SET status=?, step=?, report_json=?, "
                     "updated_at=CURRENT_TIMESTAMP WHERE project_id=?",
                     (state, step, json.dumps(report, ensure_ascii=False), pid))


def _client():
    # Import lazily: app registers all feature blueprints at startup.
    import app as application
    return application.app.test_client()


def _invoke(client, method, url, data=None):
    response = client.open(url, method=method, json=data)
    output = response.get_json(silent=True) or {}
    if response.status_code not in (200, 201, 202):
        detail = output.get("error") or (
            f"Stage endpoint {url} returned HTTP {response.status_code}; "
            "confirm that the Creator Studio feature route is installed.")
        raise AutoError(str(detail)[:280])
    return output


def _stage(pid, stage):
    return db.query_one(
        "SELECT status, artifact_json, error FROM creator_stages "
        "WHERE project_id=? AND stage=?", (pid, stage))


def _poll(pid, stage, job, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        row = _stage(pid, stage)
        if row and row["status"] == "done":
            return row
        if row and row["status"] == "failed":
            raise AutoError((row["error"] or "Stage failed.")[:280])
        project = render._project(pid)
        if not project:
            raise AutoError("Project was deleted.")
        time.sleep(0.4)
    raise AutoError(f"{stage} exceeded the local processing time limit.")


def _step(pid, step, report):
    report["completed"] = list(report.get("completed", []))
    _save(pid, "running", step, report)


def _automate(pid, options):
    client = _client()
    base = f"/api/creator/projects/{pid}"
    report = {"completed": [], "needs_review": True}
    current = "script"
    try:
        current = "script"
        _step(pid, current, report)
        project = render._project(pid)
        if not project:
            raise AutoError("Project not found.")
        if not project["script_json"]:
            _invoke(client, "POST", base + "/script/generate", {})
            _poll(pid, "script", report, WAIT_SECONDS["script"])
        report["completed"].append("script")

        current = "narration"
        _step(pid, current, report)
        narration = _stage(pid, "narration")
        if not narration or narration["status"] != "done":
            args = {}
            if options["voice_engine"]:
                args["engine"] = options["voice_engine"]
            if options["voice"]:
                args["voice"] = options["voice"]
            _invoke(client, "POST", base + "/narration/generate", args)
            _poll(pid, "narration", report, WAIT_SECONDS["narration"])
        report["completed"].append("narration")

        current = "footage"
        _step(pid, current, report)
        script = json.loads(render._project(pid)["script_json"])
        slots = render._slots(script)

        def missing_slots():
            return [slot for slot in slots if not render._visuals(pid).get(slot)]

        if options.get("visual_mode") == "neural_video":
            # Resume from validated neural footage instead of paying the inference
            # cost again when a later stage such as music or rendering fails.
            visual_row = _stage(pid, "visuals")
            visual_artifact = (json.loads(visual_row["artifact_json"])
                               if visual_row and visual_row["artifact_json"] else {})
            previous_clips = visual_artifact.get("clips", {})
            previous_sources = visual_artifact.get("sources", {})
            reusable = (all(slot in previous_clips
                            and previous_sources.get(slot, {}).get("provider") == "local-cogvideox-2b"
                            for slot in slots)
                        and all((render._root(pid) / "visuals" / previous_clips[slot]).is_file()
                                for slot in slots))
            if not reusable:
                _invoke(client, "POST", base + "/neural-video/generate", {
                    "approve_model_download": options["approve_image_model_download"]})
            # True diffusion inference is much slower than FFmpeg still-image animation.
            if not reusable:
                deadline = time.monotonic() + 21600
                while time.monotonic() < deadline:
                    job = db.query_one(
                        "SELECT status,report_json FROM creator_video_jobs WHERE project_id=?",
                        (pid,))
                    if job and job["status"] in ("done", "blocked", "interrupted"):
                        break
                    time.sleep(1)
                else:
                    raise AutoError("Neural video exceeded the 6-hour project limit.")
                if job["status"] != "done":
                    detail = json.loads(job["report_json"] or "{}")
                    err = next(iter(detail.get("errors", {}).values()),
                               "Neural video generation failed.")
                    raise AutoError(str(err)[:280])
        elif options.get("visual_mode") == "ai":
            # Explicit AI mode regenerates *every* script slot, even if stock
            # or manually uploaded footage already exists in this project.
            # The image worker swaps references only after all scenes succeed.
            _invoke(client, "POST", base + "/images/fill", {
                "approve_model_download": options["approve_image_model_download"],
                "replace_existing": True,
            })
            deadline = time.monotonic() + WAIT_SECONDS["footage"]
            while time.monotonic() < deadline:
                job = db.query_one(
                    "SELECT status, report_json FROM creator_image_jobs WHERE project_id=?",
                    (pid,))
                if job and job["status"] in ("done", "blocked", "interrupted"):
                    break
                time.sleep(0.4)
            else:
                raise AutoError("AI-only scene generation timed out.")
            if job["status"] != "done":
                detail = json.loads(job["report_json"] or "{}")
                error = next(iter(detail.get("errors", {}).values()),
                             "AI-only scene generation failed.")
                raise AutoError(str(error)[:280])
        else:
            if missing_slots() and options["approve_stock_download"]:
                from . import creator_providers
                if not creator_providers.configured() and not options["use_ai_images"]:
                    raise AutoError("Configure a free stock API key or enable local AI image fallback.")
                if creator_providers.configured():
                    for attempt in range(MAX_BATCHES):
                        before = missing_slots()
                        if not before:
                            break
                        _invoke(client, "POST", base + "/stock/auto-fill",
                                {"approve_download": True})
                        deadline = time.monotonic() + WAIT_SECONDS["footage"]
                        while time.monotonic() < deadline:
                            job = db.query_one(
                                "SELECT status, report_json FROM creator_stock_jobs "
                                "WHERE project_id=?", (pid,))
                            if job and job["status"] in ("done", "failed"):
                                break
                            time.sleep(0.4)
                        else:
                            raise AutoError("Stock footage lookup timed out.")
                        after = missing_slots()
                        if job["status"] == "failed" or len(after) >= len(before):
                            # Optional local image generation can provide the missing
                            # scenes when stock services cannot; never repeat bad results.
                            if not options["use_ai_images"]:
                                raise AutoError("Stock footage is unavailable; upload missing scenes or enable AI image fallback.")
                            break

            if missing_slots() and options["use_ai_images"]:
                _invoke(client, "POST", base + "/images/fill", {
                    "approve_model_download": options["approve_image_model_download"]})
                deadline = time.monotonic() + WAIT_SECONDS["footage"]
                while time.monotonic() < deadline:
                    job = db.query_one(
                        "SELECT status, report_json FROM creator_image_jobs WHERE project_id=?",
                        (pid,))
                    if job and job["status"] in ("done", "blocked", "interrupted"):
                        break
                    time.sleep(0.4)
                else:
                    raise AutoError("Local AI image generation timed out.")
                if job["status"] != "done":
                    detail = json.loads(job["report_json"] or "{}")
                    error = next(iter(detail.get("errors", {}).values()),
                                 "Some AI image scenes could not be generated.")
                    raise AutoError(str(error)[:280])

            if missing_slots():
                if not options["approve_stock_download"] and not options["use_ai_images"]:
                    raise AutoError(
                        "Missing scene footage. Approve stock downloads or enable local AI image generation.")
                raise AutoError("Some scenes still lack footage; review footage and image reports.")
        report["completed"].append("footage")

        current = "music"
        _step(pid, current, report)
        if options["music"] == "instrumental" and not render._music(pid):
            from . import creator_music_api
            _invoke(client, "POST", base + "/music/generate", {
                "mood": "cinematic", "seconds": 30})
        if options["music"] == "neural" and not render._music(pid):
            _invoke(client, "POST", base + "/music/neural/generate", {
                "mood": "cinematic", "duration": "match",
                "download_model": options["approve_model_download"],
                "sample_seconds": 12,
            })
            deadline = time.monotonic() + WAIT_SECONDS["music"]
            while time.monotonic() < deadline:
                job = db.query_one(
                    "SELECT status, report_json FROM creator_music_jobs WHERE project_id=?",
                    (pid,))
                if job and job["status"] in ("done", "failed"):
                    break
                time.sleep(0.4)
            else:
                raise AutoError("Local neural soundtrack generation timed out.")
            if job["status"] == "failed":
                info = json.loads(job["report_json"] or "{}")
                raise AutoError(str(info.get("error") or "Neural soundtrack generation failed.")[:280])
        if options["music"] == "none" and render._music(pid):
            _invoke(client, "DELETE", base + "/music")
        report["completed"].append("music")

        current = "captions"
        _step(pid, current, report)
        if options["align_captions"]:
            caption = _stage(pid, "captions")
            narration = json.loads(_stage(pid, "narration")["artifact_json"])
            saved = json.loads(caption["artifact_json"]) if caption and caption["artifact_json"] else {}
            if (saved.get("narration_run_id") != narration.get("run_id")
                    and narration.get("word_timing_source") != "edge"):
                _invoke(client, "POST", base + "/captions/align", {})
                deadline = time.monotonic() + WAIT_SECONDS["captions"]
                while time.monotonic() < deadline:
                    row = db.query_one("SELECT status FROM creator_projects WHERE id=?", (pid,))
                    if row and row["status"] != "aligning_captions":
                        break
                    time.sleep(0.4)
                else:
                    raise AutoError("Whisper subtitle alignment timed out.")
                cap = _stage(pid, "captions")
                if not cap or cap["status"] != "done":
                    raise AutoError("Whisper alignment did not finish successfully.")
        report["completed"].append("captions")

        current = "render"
        _step(pid, current, report)
        existing = _stage(pid, "render")
        if not existing or existing["status"] != "done":
            _invoke(client, "POST", base + "/render", {})
            _poll(pid, "render", report, WAIT_SECONDS["render"])
        # A completed stage must have an actual downloadable file.
        # Avoid buffering a large MP4 into process memory.
        export_response = client.get(base + "/render/file", buffered=False)
        try:
            if export_response.status_code != 200:
                raise AutoError("Rendering finished but its MP4 is unavailable. Check the export files.")
        finally:
            export_response.close()
        report["completed"].append("render")
        current = "review"
        report["message"] = "Video rendered. Review and approve manually before any publishing."
        _save(pid, "done", current, report)
    except Exception as exc:
        log.exception("Creator autonomous job stopped for project %s at %s", pid, current)
        report["error"] = str(exc)[:300] if isinstance(exc, (AutoError, ValueError)) else "Production failed; inspect local logs."
        _save(pid, "blocked", current, report)


@bp.post("/api/creator/projects/<int:pid>/auto/start")
def start(pid):
    try:
        options = validate(request.get_json(silent=True))
    except AutoError as exc:
        return jsonify(error=str(exc)), 400
    with _lock:
        with db.get_db() as conn:
            project = conn.execute("SELECT status FROM creator_projects WHERE id=?", (pid,)).fetchone()
            if not project:
                return jsonify(error="Project not found."), 404
            if project["status"] in render.BUSY:
                return jsonify(error="Another Creator Studio stage is active."), 409
            current = conn.execute("SELECT status FROM creator_auto_jobs WHERE project_id=?", (pid,)).fetchone()
            if current and current["status"] == "running":
                return jsonify(error="An automatic production job is already running."), 409
            conn.execute("INSERT INTO creator_auto_jobs(project_id,status,step,options_json,report_json) "
                         "VALUES (?,'running','script',?,'{}') "
                         "ON CONFLICT(project_id) DO UPDATE SET status='running',step='script',"
                         "options_json=excluded.options_json,report_json='{}',updated_at=CURRENT_TIMESTAMP",
                         (pid, json.dumps(options)))
        _executor.submit(_automate, pid, options)
    return jsonify(ok=True, status="running", step="script"), 202


@bp.get("/api/creator/projects/<int:pid>/auto")
def auto_status(pid):
    if not render._project(pid):
        return jsonify(error="Project not found."), 404
    return jsonify(status(pid))
