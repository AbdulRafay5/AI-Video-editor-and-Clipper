"""Creator Studio: persistent topic-to-script stage, entirely local with Ollama."""
import json
import logging
import shutil

import config
from concurrent.futures import ThreadPoolExecutor
from flask import Blueprint, jsonify, request

from . import db, llm, creator_inference

bp = Blueprint("creator", __name__)
log = logging.getLogger("clipper.creator")
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="creator")
STAGES = ("script", "narration", "visuals", "captions", "music", "render")

@bp.get("/api/creator/diagnostics")
def creator_diagnostics():
    """Local-only, read-only prerequisites; no downloads or model inference."""
    from . import local_imagegen, local_musicgen, local_text_to_video
    ffmpeg = bool(shutil.which("ffmpeg"))
    ffprobe = bool(shutil.which("ffprobe"))
    ollama = llm.status()
    images = local_imagegen.dependencies_ready()
    music = local_musicgen.dependencies_ready()
    neural_video = local_text_to_video.dependencies_ready()
    return jsonify(
        ffmpeg=ffmpeg, ffprobe=ffprobe,
        ollama={"running": ollama["running"], "installed": ollama["installed"],
                "model": ollama["model"], "message": ollama["message"]},
        optional={"ai_images": images, "musicgen": music,
                  "neural_video": neural_video},
        ready_for_render=ffmpeg and ffprobe,
        ready_for_ai_script=ollama["running"] and ollama["installed"],
        note="Dependency checks only; model files and GPU inference are not validated."
    )


SCRIPT_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "hook": {"type": "string"},
        "scenes": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "narration": {"type": "string"},
                "visual_idea": {"type": "string"}
            },
            "required": ["narration", "visual_idea"],
            "additionalProperties": False,
        }},
        "closing": {"type": "string"}
    },
    "required": ["title", "hook", "scenes", "closing"],
    "additionalProperties": False,
}


def validate_script(payload):
    if not isinstance(payload, dict):
        raise ValueError("Script must be an object.")
    title, hook, closing = (payload.get(k) for k in ("title", "hook", "closing"))
    if any(not isinstance(v, str) or not v.strip() or len(v) > 500
           for v in (title, hook, closing)):
        raise ValueError("Script needs a title, hook and closing (each under 500 characters).")
    scenes = payload.get("scenes")
    if not isinstance(scenes, list) or not 1 <= len(scenes) <= 12:
        raise ValueError("Script needs between 1 and 12 scenes.")
    clean = []
    for scene in scenes:
        if not isinstance(scene, dict):
            raise ValueError("Invalid scene.")
        narration, visual = scene.get("narration"), scene.get("visual_idea")
        if (not isinstance(narration, str) or not 5 <= len(narration.strip()) <= 1600
                or not isinstance(visual, str) or not 2 <= len(visual.strip()) <= 350):
            raise ValueError("Every scene needs spoken narration and a visual suggestion.")
        clean.append({"narration": narration.strip(), "visual_idea": visual.strip()})
    total = sum(len(s["narration"]) for s in clean) + len(hook) + len(closing)
    if total > 8500:
        raise ValueError("Script is too long.")
    return {"title": title.strip(), "hook": hook.strip(),
            "scenes": clean, "closing": closing.strip()}


def _init_stages(conn, project_id):
    conn.executemany(
        "INSERT INTO creator_stages (project_id, stage) VALUES (?, ?)",
        [(project_id, s) for s in STAGES])


def _get_project(pid):
    project = db.query_one("SELECT * FROM creator_projects WHERE id=?", (pid,))
    if not project:
        return None
    stages = db.query("SELECT stage, status, error, updated_at FROM creator_stages WHERE project_id=?", (pid,))
    project["script"] = json.loads(project.pop("script_json")) if project["script_json"] else None
    project["stages"] = {s["stage"]: {k: v for k, v in s.items() if k != "stage"} for s in stages}
    return project


@bp.get("/api/creator/projects")
def list_creator_projects():
    return jsonify(projects=db.query(
        "SELECT id, topic, audience, target_seconds, status, error, created_at, updated_at "
        "FROM creator_projects ORDER BY id DESC LIMIT 100"))


@bp.post("/api/creator/projects")
def create_creator_project():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify(error="Provide a project topic."), 400
    topic = body.get("topic")
    audience = body.get("audience", "general")
    seconds = body.get("target_seconds", 60)
    if not isinstance(topic, str) or not 5 <= len(topic.strip()) <= 300:
        return jsonify(error="Topic must be 5–300 characters."), 400
    if not isinstance(audience, str) or not 1 <= len(audience.strip()) <= 100:
        return jsonify(error="Audience must be 1–100 characters."), 400
    if type(seconds) is not int or not 15 <= seconds <= 300:
        return jsonify(error="Length must be between 15 and 300 seconds."), 400
    with db.get_db() as conn:
        cur = conn.execute(
            "INSERT INTO creator_projects(topic, audience, target_seconds) VALUES (?,?,?)",
            (topic.strip(), audience.strip(), seconds))
        pid = cur.lastrowid
        _init_stages(conn, pid)
    return jsonify(project=_get_project(pid)), 201


@bp.get("/api/creator/projects/<int:pid>")
def get_creator_project(pid):
    project = _get_project(pid)
    return jsonify(project=project) if project else (jsonify(error="Project not found."), 404)


def _run_script(pid):
    try:
        project = db.query_one("SELECT topic, audience, target_seconds FROM creator_projects WHERE id=?", (pid,))
        if not project:
            return
        system = (
            "You are a professional short-form video scriptwriter. Draft a coherent, engaging "
            "original script for the user's topic. Aim for the requested spoken length; assume "
            "approximately 140 spoken words per minute. Make the first line a natural hook. "
            "Give a concise closing, and 3 to 8 coherent scenes. Provide practical real-world "
            "visual ideas, not fabricated evidence, generic artificial stock tropes, or misleading "
            "representations of actual people. Do not invent factual claims or statistics. "
            "Use neutral language when discussing unverified facts. No advertisements, "
            "copyrighted lyrics or instructions to use paid services. Respond with JSON only."
        )
        answer, _ = llm.chat_json(
            system,
            "Topic: " + project["topic"] + "\nAudience: " + project["audience"]
            + "\nTarget narration duration: " + str(project["target_seconds"]) + " seconds.",
            SCRIPT_SCHEMA, timeout=240)
        script = validate_script(answer)
        with db.get_db() as conn:
            conn.execute("UPDATE creator_projects SET script_json=?, status='script_ready', "
                         "error=NULL, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                         (json.dumps(script, ensure_ascii=False), pid))
            conn.execute("UPDATE creator_stages SET status='done', artifact_json=?, error=NULL, "
                         "updated_at=CURRENT_TIMESTAMP WHERE project_id=? AND stage='script'",
                         (json.dumps(script, ensure_ascii=False), pid))
            conn.execute("UPDATE creator_stages SET status='pending', artifact_json=NULL, error=NULL "
                         "WHERE project_id=? AND stage!='script'", (pid,))
    except Exception as exc:
        log.exception("Creator script failed for project %s", pid)
        detail = str(exc)[:300] if isinstance(exc, (ValueError, llm.LLMError)) else "Script generation failed; see the local log."
        with db.get_db() as conn:
            conn.execute("UPDATE creator_projects SET status='failed', error=?, "
                         "updated_at=CURRENT_TIMESTAMP WHERE id=?", (detail, pid))
            conn.execute("UPDATE creator_stages SET status='failed', error=?, "
                         "updated_at=CURRENT_TIMESTAMP WHERE project_id=? AND stage='script'",
                         (detail, pid))


@bp.post("/api/creator/projects/<int:pid>/script/generate")
def generate_script(pid):
    with db.get_db() as conn:
        state = conn.execute("SELECT status FROM creator_projects WHERE id=?", (pid,)).fetchone()
        if not state:
            return jsonify(error="Project not found."), 404
        if state["status"] in ("generating_script", "generating_narration", "rendering", "sourcing_visuals", "aligning_captions", "generating_music", "generating_images", "generating_video"):
            return jsonify(error="Another generation is already running."), 409
    readiness = llm.status()
    if not (readiness["running"] and readiness["installed"]):
        return jsonify(error=readiness["message"]), 503
    with db.get_db() as conn:
        cur = conn.execute(
            "UPDATE creator_projects SET status='generating_script', error=NULL, "
            "updated_at=CURRENT_TIMESTAMP WHERE id=? AND status NOT IN ('generating_script','generating_narration','rendering','sourcing_visuals','aligning_captions','generating_music','generating_images','generating_video')", (pid,))
        if not cur.rowcount:
            return jsonify(error="Script generation is already running."), 409
        conn.execute("UPDATE creator_stages SET status='running', error=NULL, "
                     "updated_at=CURRENT_TIMESTAMP WHERE project_id=? AND stage='script'", (pid,))
    try:
        _executor.submit(creator_inference.run_exclusive, _run_script, pid)
    except RuntimeError:
        # The executor can reject submissions during shutdown. Restore the
        # last saved script, not a permanently busy phantom job.
        message = "Script worker unavailable; restart the application."
        with db.get_db() as conn:
            has_script = conn.execute(
                "SELECT script_json FROM creator_projects WHERE id=?", (pid,)
            ).fetchone()
            ready = "script_ready" if has_script and has_script["script_json"] else "draft"
            conn.execute(
                "UPDATE creator_projects SET status=?, error=?, "
                "updated_at=CURRENT_TIMESTAMP "
                "WHERE id=? AND status='generating_script'",
                (ready, message, pid))
            conn.execute(
                "UPDATE creator_stages SET status=?, error=?, "
                "updated_at=CURRENT_TIMESTAMP "
                "WHERE project_id=? AND stage='script' AND status='running'",
                ("done" if ready == "script_ready" else "failed",
                 message, pid))
        return jsonify(error=message), 503
    return jsonify(ok=True, status="generating_script"), 202


@bp.put("/api/creator/projects/<int:pid>/script")
def save_script(pid):
    body = request.get_json(silent=True)
    try:
        script = validate_script(body)
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    with db.get_db() as conn:
        state = conn.execute("SELECT status FROM creator_projects WHERE id=?", (pid,)).fetchone()
        if not state:
            return jsonify(error="Project not found."), 404
        if state["status"] in ("generating_script", "generating_narration", "rendering", "sourcing_visuals", "aligning_captions", "generating_music", "generating_images", "generating_video"):
            return jsonify(error="Wait until the current generation finishes."), 409
        conn.execute("UPDATE creator_projects SET script_json=?, status='script_ready', "
                     "error=NULL, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                     (json.dumps(script, ensure_ascii=False), pid))
        conn.execute("UPDATE creator_stages SET status='done', artifact_json=?, error=NULL, "
                     "updated_at=CURRENT_TIMESTAMP WHERE project_id=? AND stage='script'",
                     (json.dumps(script, ensure_ascii=False), pid))
        # Any future generation derived from an older script would be invalid.
        conn.execute("UPDATE creator_stages SET status='pending', artifact_json=NULL, error=NULL "
                     "WHERE project_id=? AND stage!='script'", (pid,))
    return jsonify(project=_get_project(pid))


@bp.delete("/api/creator/projects/<int:pid>")
def delete_creator_project(pid):
    from . import creator_render_api as render
    # Serialize against media upload, stock rejection and image replacement.
    with render._upload_lock:
        with db.get_db() as conn:
            state = conn.execute("SELECT status FROM creator_projects WHERE id=?", (pid,)).fetchone()
            if not state:
                return jsonify(error="Project not found."), 404
            if state["status"] in render.BUSY:
                return jsonify(error="Finish the active Creator Studio stage first."), 409
            active_auto = conn.execute(
                "SELECT 1 FROM creator_auto_jobs WHERE project_id=? AND status='running'",
                (pid,)).fetchone()
            active_batch = conn.execute(
                "SELECT 1 FROM creator_batch_items WHERE project_id=? "
                "AND status IN ('queued','running') LIMIT 1", (pid,)).fetchone()
            if active_auto or active_batch:
                return jsonify(error="Finish or remove this project from its active batch first."), 409
            conn.execute("DELETE FROM creator_projects WHERE id=?", (pid,))
        # Never remove a path supplied by a client. Resolve a single known
        # project directory below the configured Creator Studio data root.
        parent = (config.DATA_DIR / "creator").resolve()
        folder = parent / str(pid)
        if folder.is_symlink():
            log.warning("Not deleting symlinked Creator data folder for project %s", pid)
            return jsonify(ok=True, warning="Project deleted; symlinked media folder was retained.")
        try:
            if folder.exists():
                shutil.rmtree(folder)
        except OSError:
            log.exception("Couldn't delete Creator project media for %s", pid)
            return jsonify(ok=True, warning="Project deleted, but local media cleanup failed.")
    return jsonify(ok=True)
