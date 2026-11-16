"""Persistent, sequential Creator Studio batch production.

A batch holds independent Creator Studio projects, each of which uses the
existing automatic stages. No auto-publishing or concurrent neural inference.
"""
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from flask import Blueprint, current_app, jsonify, request

from . import creator_api, creator_auto_api as auto, db

bp = Blueprint("creator_batch", __name__)
log = logging.getLogger("clipper.creator_batch")
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="creator-batch")
_lock = threading.RLock()
TERMINAL = {"done", "blocked", "interrupted"}
MAX_TOPICS = 8


def _details(batch_id):
    batch = db.query_one("SELECT * FROM creator_batches WHERE id=?", (batch_id,))
    if not batch:
        return None
    items = db.query(
        "SELECT i.id, i.project_id, i.status, i.error, p.topic, "
        "p.status AS project_status, a.step AS current_step "
        "FROM creator_batch_items i JOIN creator_projects p ON p.id=i.project_id "
        "LEFT JOIN creator_auto_jobs a ON a.project_id=i.project_id "
        "WHERE i.batch_id=? ORDER BY i.id", (batch_id,))
    result = {key: batch[key] for key in ("id", "status", "created_at", "updated_at")}
    result["items"] = items
    result["counts"] = {
        state: sum(item["status"] == state for item in items)
        for state in ("queued", "running", "done", "blocked", "interrupted")
    }
    return result


def _set_item(item_id, status, error=None):
    db.execute(
        "UPDATE creator_batch_items SET status=?,error=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (status, error, item_id))


def _run(batch_id, app):
    # Reuse the active app: reimporting app during python app.py execution
    # would initialise a second app and trigger database restart recovery.
    with app.test_client() as client:
        try:
            batch = db.query_one("SELECT * FROM creator_batches WHERE id=?", (batch_id,))
            if not batch or batch["status"] != "running":
                return
            options = json.loads(batch["options_json"])
            items = db.query(
                "SELECT id,project_id FROM creator_batch_items "
                "WHERE batch_id=? AND status='queued' ORDER BY id", (batch_id,))
            for item in items:
                # Leave prior completed videos unchanged, and only process queued items.
                bid = db.query_one("SELECT status FROM creator_batches WHERE id=?", (batch_id,))
                if not bid or bid["status"] != "running":
                    return
                _set_item(item["id"], "running")
                pid = item["project_id"]
                try:
                    response = client.post(
                        f"/api/creator/projects/{pid}/auto/start", json=options)
                    if response.status_code != 202:
                        body = response.get_json(silent=True) or {}
                        raise ValueError(str(body.get("error") or "Cannot start project.")[:300])
                    # Neural generation may take hours with CPU offloading.
                    deadline = time.monotonic() + 24 * 3600
                    while time.monotonic() < deadline:
                        state = auto.status(pid)
                        if state["status"] in TERMINAL:
                            break
                        time.sleep(1)
                    else:
                        raise ValueError("Project exceeded the batch processing time limit.")
                    if state["status"] != "done":
                        raise ValueError(str(state["report"].get("error") or
                                             "Project did not complete.")[:300])
                    _set_item(item["id"], "done")
                except Exception as exc:
                    log.exception("Batch %s item %s failed", batch_id, item["id"])
                    _set_item(item["id"], "blocked", str(exc)[:300])
            db.execute(
                "UPDATE creator_batches SET status=CASE WHEN EXISTS("
                "SELECT 1 FROM creator_batch_items WHERE batch_id=? AND status!='done'"
                ") THEN 'blocked' ELSE 'done' END,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (batch_id, batch_id))
        except Exception:
            log.exception("Batch coordinator %s failed unexpectedly", batch_id)
            db.execute(
                "UPDATE creator_batches SET status='blocked',updated_at=CURRENT_TIMESTAMP "
                "WHERE id=?", (batch_id,))
            db.execute(
                "UPDATE creator_batch_items SET status='interrupted',"
                "error='Batch coordinator stopped unexpectedly.',updated_at=CURRENT_TIMESTAMP "
                "WHERE batch_id=? AND status='running'", (batch_id,))


@bp.post("/api/creator/batches")
def create_batch():
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or set(body) - {"topics", "audience", "target_seconds", "options"}:
        return jsonify(error="Provide a list of topics and production options."), 400
    topics = body.get("topics")
    if (not isinstance(topics, list) or not 2 <= len(topics) <= MAX_TOPICS
            or any(not isinstance(t, str) or not 5 <= len(t.strip()) <= 300
                   for t in topics)):
        return jsonify(error="Provide 2–8 topics, 5–300 characters each."), 400
    audience = body.get("audience", "general")
    seconds = body.get("target_seconds", 30)
    if not isinstance(audience, str) or not 1 <= len(audience.strip()) <= 100:
        return jsonify(error="Audience must be 1–100 characters."), 400
    if type(seconds) is not int or not 15 <= seconds <= 300:
        return jsonify(error="Video duration must be 15–300 seconds."), 400
    try:
        options = auto.validate(body.get("options", {}))
    except auto.AutoError as exc:
        return jsonify(error=str(exc)), 400
    with db.get_db() as conn:
        cur = conn.execute(
            "INSERT INTO creator_batches(status,options_json) VALUES ('running',?)",
            (json.dumps(options),))
        batch_id = cur.lastrowid
        for topic in topics:
            project_cur = conn.execute(
                "INSERT INTO creator_projects(topic,audience,target_seconds) VALUES (?,?,?)",
                (topic.strip(), audience.strip(), seconds))
            pid = project_cur.lastrowid
            creator_api._init_stages(conn, pid)
            conn.execute(
                "INSERT INTO creator_batch_items(batch_id,project_id) VALUES (?,?)",
                (batch_id, pid))
    _executor.submit(_run, batch_id, current_app._get_current_object())
    return jsonify(batch=_details(batch_id)), 202


@bp.get("/api/creator/batches")
def list_batches():
    rows = db.query(
        "SELECT id,status,created_at,updated_at FROM creator_batches ORDER BY id DESC LIMIT 30")
    return jsonify(batches=rows)


@bp.get("/api/creator/batches/<int:batch_id>")
def batch_status(batch_id):
    details = _details(batch_id)
    return jsonify(batch=details) if details else (jsonify(error="Batch not found."), 404)


@bp.post("/api/creator/batches/<int:batch_id>/retry")
def retry_batch(batch_id):
    with _lock:
        with db.get_db() as conn:
            row = conn.execute(
                "SELECT status FROM creator_batches WHERE id=?", (batch_id,)).fetchone()
            if not row:
                return jsonify(error="Batch not found."), 404
            if row["status"] == "running":
                return jsonify(error="This batch is already running."), 409
            pending = conn.execute(
                "SELECT COUNT(*) AS n FROM creator_batch_items WHERE batch_id=? "
                "AND status IN ('blocked','interrupted','queued')", (batch_id,)).fetchone()["n"]
            if not pending:
                return jsonify(error="All batch items have already completed."), 409
            conn.execute(
                "UPDATE creator_batch_items SET status='queued',error=NULL,"
                "updated_at=CURRENT_TIMESTAMP WHERE batch_id=? "
                "AND status IN ('blocked','interrupted')", (batch_id,))
            conn.execute(
                "UPDATE creator_batches SET status='running',updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (batch_id,))
        _executor.submit(_run, batch_id, current_app._get_current_object())
    return jsonify(batch=_details(batch_id)), 202
