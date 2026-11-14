"""Opt-in local image fallback for missing Creator Studio scene footage."""
import json
import logging
import uuid
from concurrent.futures import ThreadPoolExecutor

from flask import Blueprint, jsonify, request

from . import creator_render_api as creator, db, local_imagegen as imagegen

bp = Blueprint("creator_images", __name__)
log = logging.getLogger("clipper.creator_images")
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="creator-images")
MAX_SCENES = 14


def _state(pid, status, report):
    with db.get_db() as conn:
        conn.execute(
            "UPDATE creator_image_jobs SET status=?,report_json=?,updated_at=CURRENT_TIMESTAMP "
            "WHERE project_id=?", (status, json.dumps(report), pid))


def _generate(pid, allow_download):
    report = {"generated": {}, "errors": {}, "model": imagegen.MODEL}
    try:
        project = creator._project(pid)
        if not project or not project["script_json"]:
            raise imagegen.ImageGenerationError("Create a script first.")
        script = json.loads(project["script_json"])
        slots = creator._slots(script)
        for slot in slots[:MAX_SCENES]:
            if creator._visuals(pid).get(slot):
                continue
            prompt = imagegen.image_prompt(slot, script)
            visual_dir = creator._root(pid) / "visuals"
            visual_dir.mkdir(parents=True, exist_ok=True)
            name = "clip_" + uuid.uuid4().hex
            picture = visual_dir / (name + ".png")
            movie = visual_dir / (name + ".mp4")
            try:
                result = imagegen.generate_image(
                    prompt, picture, allow_download=allow_download)
                imagegen.animate_still(picture, movie, seconds=5)
                # Save the scene only after both files have been generated.
                with creator._upload_lock:
                    with db.get_db() as conn:
                        row = conn.execute(
                            "SELECT artifact_json FROM creator_stages WHERE project_id=? "
                            "AND stage='visuals'", (pid,)).fetchone()
                        prior = json.loads(row["artifact_json"]) if row and row["artifact_json"] else {}
                        clips = prior.get("clips", {})
                        sources = prior.get("sources", {})
                        if slot not in clips:
                            clips[slot] = movie.name
                            sources[slot] = {"provider": "local-sd-turbo",
                                             "prompt": prompt, "image": picture.name,
                                             "model": result["model"]}
                            conn.execute(
                                "UPDATE creator_stages SET status=?,artifact_json=?,error=NULL,"
                                "updated_at=CURRENT_TIMESTAMP WHERE project_id=? AND stage='visuals'",
                                ("done" if all(clips.get(s) for s in slots) else "pending",
                                 json.dumps({"clips": clips, "sources": sources}), pid))
                            conn.execute(
                                "UPDATE creator_stages SET status='pending',artifact_json=NULL,error=NULL "
                                "WHERE project_id=? AND stage='render'", (pid,))
                            report["generated"][slot] = movie.name
                        else:
                            movie.unlink(missing_ok=True)
                            picture.unlink(missing_ok=True)
            except (imagegen.ImageGenerationError, OSError, ValueError) as exc:
                movie.unlink(missing_ok=True)
                picture.unlink(missing_ok=True)
                report["errors"][slot] = str(exc)[:250]
                # Missing model or hardware should not repeat costly failure
                # on every scene. The user can retry after fixing the issue.
                break
            _state(pid, "running", report)
        report["remaining"] = [s for s in slots if not creator._visuals(pid).get(s)]
        _state(pid, "done" if not report["remaining"] else "blocked", report)
    except Exception:
        log.exception("Local image fallback failed for project %s", pid)
        report["errors"]["general"] = "Local image generation failed; see application logs."
        _state(pid, "blocked", report)
    finally:
        with creator._upload_lock:
            with db.get_db() as conn:
                previous = conn.execute(
                    "SELECT status FROM creator_stages WHERE project_id=? AND stage='narration'",
                    (pid,)).fetchone()
                ready = "narration_ready" if previous and previous["status"] == "done" else "script_ready"
                conn.execute(
                    "UPDATE creator_projects SET status=?,updated_at=CURRENT_TIMESTAMP "
                    "WHERE id=? AND status='generating_images'", (ready, pid))


@bp.get("/api/creator/images/capabilities")
def capabilities():
    return jsonify(model=imagegen.MODEL, installed=imagegen.dependencies_ready(),
                   free_local=True, initial_download_requires_approval=True,
                   note="Generate 512px images, then animate them into scene MP4s.")


@bp.get("/api/creator/projects/<int:pid>/images")
def image_status(pid):
    if not creator._project(pid):
        return jsonify(error="Project not found."), 404
    row = db.query_one("SELECT status,report_json FROM creator_image_jobs WHERE project_id=?", (pid,))
    return jsonify(status=row["status"] if row else "pending",
                   report=json.loads(row["report_json"]) if row else {})


@bp.post("/api/creator/projects/<int:pid>/images/fill")
def fill(pid):
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or set(payload) != {"approve_model_download"} or \
            type(payload.get("approve_model_download")) is not bool:
        return jsonify(error="Provide explicit model-download permission (true or false)."), 400
    if not imagegen.dependencies_ready():
        return jsonify(error="Install requirements-images.txt for local image creation."), 503
    with creator._upload_lock:
        with db.get_db() as conn:
            project = conn.execute(
                "SELECT status,script_json FROM creator_projects WHERE id=?", (pid,)).fetchone()
            if not project:
                return jsonify(error="Project not found."), 404
            if project["status"] in creator.BUSY:
                return jsonify(error="Finish the current Creator Studio stage first."), 409
            if not project["script_json"]:
                return jsonify(error="Generate a script first."), 409
            script = json.loads(project["script_json"])
            if all(creator._visuals(pid).get(slot) for slot in creator._slots(script)):
                return jsonify(error="All scenes already have footage."), 409
            conn.execute(
                "UPDATE creator_projects SET status='generating_images',error=NULL,"
                "updated_at=CURRENT_TIMESTAMP WHERE id=?", (pid,))
            conn.execute(
                "INSERT INTO creator_image_jobs(project_id,status,report_json) "
                "VALUES (?,'running','{}') ON CONFLICT(project_id) DO UPDATE SET "
                "status='running',report_json='{}',updated_at=CURRENT_TIMESTAMP", (pid,))
        _executor.submit(_generate, pid, payload["approve_model_download"])
    return jsonify(status="generating_images", ok=True), 202
