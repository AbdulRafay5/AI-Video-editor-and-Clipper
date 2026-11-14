"""Optional local CogVideoX neural footage for every Creator Studio scene.

Follows the scene->material->MP4 shape used by MoneyPrinterTurbo's text-to-video
adapters, but executes a downloaded model locally; no chargeable endpoints.
"""
import json
import logging
import uuid
from concurrent.futures import ThreadPoolExecutor

from flask import Blueprint, jsonify, request

from . import creator_render_api as creator, db, local_text_to_video as engine

bp = Blueprint("creator_neural_video", __name__)
log = logging.getLogger("clipper.creator_neural_video")
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="creator-neural-video")
MAX_SLOTS = 14


def _state(pid, status, report):
    with db.get_db() as conn:
        conn.execute(
            "INSERT INTO creator_video_jobs(project_id,status,report_json) VALUES (?,?,?) "
            "ON CONFLICT(project_id) DO UPDATE SET status=excluded.status,"
            "report_json=excluded.report_json,updated_at=CURRENT_TIMESTAMP",
            (pid, status, json.dumps(report)))


def _run(pid, approve_download):
    report = {"model": engine.MODEL, "generated": {}, "errors": {},
              "mode": "neural_text_to_video", "needs_review": True}
    created = []
    swapped = False
    try:
        project = creator._project(pid)
        if not project or not project["script_json"]:
            raise engine.VideoGenerationError("Save the script before generating video.")
        script_json = project["script_json"]
        script = json.loads(script_json)
        slots = creator._slots(script)
        if not 1 <= len(slots) <= MAX_SLOTS:
            raise engine.VideoGenerationError("Too many scenes to generate in one run.")
        folder = creator._root(pid) / "visuals"
        folder.mkdir(parents=True, exist_ok=True)
        clips = {}
        sources = {}
        # Unload Ollama's LLM to free shared GPU memory before loading CogVideoX.
        from . import llm
        llm.unload()
        with engine.LocalCogVideoX(allow_download=approve_download) as model:
            for slot in slots:
                prompt = engine.prompt_for(slot, script)
                target = folder / ("clip_" + uuid.uuid4().hex + ".mp4")
                created.append(target)
                info = model.generate(prompt, target)
                clips[slot] = target.name
                sources[slot] = {"provider": "local-cogvideox-2b",
                                 "prompt": prompt, "model": engine.MODEL,
                                 "duration_seconds": info["seconds"]}
                report["generated"][slot] = target.name
                _state(pid, "running", report)
        with creator._upload_lock:
            with db.get_db() as conn:
                current = conn.execute(
                    "SELECT script_json FROM creator_projects WHERE id=?", (pid,)
                ).fetchone()
                if not current or current["script_json"] != script_json:
                    raise engine.VideoGenerationError(
                        "Project script changed while generating; footage was not replaced.")
                # Only swap after every scene has real, validated MP4 frames.
                conn.execute(
                    "UPDATE creator_stages SET status='done',artifact_json=?,error=NULL,"
                    "updated_at=CURRENT_TIMESTAMP WHERE project_id=? AND stage='visuals'",
                    (json.dumps({"clips": clips, "sources": sources}), pid))
                conn.execute(
                    "UPDATE creator_stages SET status='pending',artifact_json=NULL,"
                    "error=NULL WHERE project_id=? AND stage='render'", (pid,))
                swapped = True
        report["remaining"] = []
        _state(pid, "done", report)
    except Exception as exc:
        log.exception("CogVideoX generation failed for project %s", pid)
        report["errors"]["general"] = (
            str(exc)[:280] if isinstance(exc, (engine.VideoGenerationError, ValueError))
            else "Neural video failed; inspect the local application log.")
        report["generated"] = {}
        _state(pid, "blocked", report)
    finally:
        if not swapped:
            for path in created:
                path.unlink(missing_ok=True)
        with creator._upload_lock:
            with db.get_db() as conn:
                narration = conn.execute(
                    "SELECT status FROM creator_stages WHERE project_id=? "
                    "AND stage='narration'", (pid,)).fetchone()
                ready = ("narration_ready" if narration and narration["status"] == "done"
                         else "script_ready")
                conn.execute(
                    "UPDATE creator_projects SET status=?,updated_at=CURRENT_TIMESTAMP "
                    "WHERE id=? AND status='generating_video'", (ready, pid))


@bp.get("/api/creator/neural-video/capabilities")
def capabilities():
    return jsonify(model=engine.MODEL, installed=engine.dependencies_ready(),
                   free_local=True, true_neural_video=True,
                   model_download_requires_approval=True,
                   frames=49, fps=8,
                   warning="6 GB VRAM is experimental; substantial CPU RAM, storage and time required.")


@bp.get("/api/creator/projects/<int:pid>/neural-video")
def status(pid):
    if not creator._project(pid):
        return jsonify(error="Project not found."), 404
    row = db.query_one(
        "SELECT status,report_json FROM creator_video_jobs WHERE project_id=?", (pid,))
    return jsonify(status=row["status"] if row else "pending",
                   report=json.loads(row["report_json"] or "{}") if row else {})


@bp.post("/api/creator/projects/<int:pid>/neural-video/generate")
def generate(pid):
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or set(body) != {"approve_model_download"} or \
            type(body.get("approve_model_download")) is not bool:
        return jsonify(error="Explicitly confirm model-download permission."), 400
    if not engine.dependencies_ready():
        return jsonify(error="Install requirements-video.txt and CUDA PyTorch first."), 503
    with creator._upload_lock:
        with db.get_db() as conn:
            project = conn.execute(
                "SELECT status,script_json FROM creator_projects WHERE id=?",
                (pid,)).fetchone()
            if not project:
                return jsonify(error="Project not found."), 404
            if project["status"] in creator.BUSY:
                return jsonify(error="Finish the current Creator Studio job first."), 409
            if not project["script_json"]:
                return jsonify(error="Generate and save a script first."), 409
            conn.execute(
                "UPDATE creator_projects SET status='generating_video',error=NULL,"
                "updated_at=CURRENT_TIMESTAMP WHERE id=?", (pid,))
            conn.execute(
                "INSERT INTO creator_video_jobs(project_id,status,report_json) "
                "VALUES (?,'running','{}') ON CONFLICT(project_id) DO UPDATE SET "
                "status='running',report_json='{}',updated_at=CURRENT_TIMESTAMP",
                (pid,))
        _executor.submit(_run, pid, body["approve_model_download"])
    return jsonify(ok=True, status="generating_video"), 202
