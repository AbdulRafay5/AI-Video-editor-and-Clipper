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
    expected = {"approve_stock_download", "voice_engine", "voice", "music", "align_captions"}
    if set(payload) - expected:
        raise AutoError("Unexpected automation option.")
    stock = payload.get("approve_stock_download", False)
    aligned = payload.get("align_captions", False)
    if type(stock) is not bool or type(aligned) is not bool:
        raise AutoError("Download approval and alignment must be true or false.")
    voice_engine = payload.get("voice_engine")
    voice = payload.get("voice")
    if voice_engine is not None and (not isinstance(voice_engine, str) or len(voice_engine) > 40):
        raise AutoError("Invalid voice engine.")
    if voice is not None and (not isinstance(voice, str) or len(voice) > 80):
        raise AutoError("Invalid voice.")
    music = payload.get("music", "keep")
    if music not in ("keep", "instrumental", "none"):
        raise AutoError("Choose existing music, local instrumental or none.")
    return {"approve_stock_download": stock, "voice_engine": voice_engine,
            "voice": voice, "music": music, "align_captions": aligned}


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
        raise AutoError(str(output.get("error") or "Production stage rejected.")[:280])
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
        if not options["approve_stock_download"]:
            script = json.loads(render._project(pid)["script_json"])
            missing = [slot for slot in render._slots(script)
                       if not render._visuals(pid).get(slot)]
            if missing:
                raise AutoError("Footage download not approved. Upload missing scene footage or approve stock downloads.")
        else:
            from . import creator_providers
            if not creator_providers.configured():
                script = json.loads(render._project(pid)["script_json"])
                if any(not render._visuals(pid).get(slot) for slot in render._slots(script)):
                    raise AutoError("Configure a free stock API key before automated sourcing.")
            for attempt in range(MAX_BATCHES):
                script = json.loads(render._project(pid)["script_json"])
                missing = [slot for slot in render._slots(script)
                           if not render._visuals(pid).get(slot)]
                if not missing:
                    break
                _invoke(client, "POST", base + "/stock/auto-fill", {"approve_download": True})
                deadline = time.monotonic() + WAIT_SECONDS["footage"]
                while time.monotonic() < deadline:
                    job = db.query_one("SELECT status, report_json FROM creator_stock_jobs "
                                       "WHERE project_id=?", (pid,))
                    if job and job["status"] in ("done", "failed"):
                        break
                    time.sleep(0.4)
                else:
                    raise AutoError("Stock footage lookup timed out.")
                if job["status"] == "failed":
                    raise AutoError("Stock footage sourcing failed; review stock report.")
                updated = render._visuals(pid)
                new_missing = [slot for slot in render._slots(script) if not updated.get(slot)]
                if len(new_missing) >= len(missing):
                    raise AutoError("Some scenes have no suitable footage. Upload or replace those scenes manually.")
            script = json.loads(render._project(pid)["script_json"])
            if any(not render._visuals(pid).get(slot) for slot in render._slots(script)):
                raise AutoError("Some footage is still missing after bounded source attempts.")
        report["completed"].append("footage")

        current = "music"
        _step(pid, current, report)
        if options["music"] == "instrumental" and not render._music(pid):
            from . import creator_music_api
            _invoke(client, "POST", base + "/music/generate", {
                "mood": "cinematic", "seconds": 30})
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
