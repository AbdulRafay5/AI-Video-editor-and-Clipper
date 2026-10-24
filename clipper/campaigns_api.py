"""Campaign profile routes: upload a brief, review the extracted rules, attach one to a project,
and read or run a clip's compliance check. Also registers every other clipper/*_api.py module."""
import importlib
import json
from pathlib import Path

from flask import Blueprint, jsonify, render_template, request

from . import campaigns as camp
from . import db, docs, jobs, llm

bp = Blueprint("campaigns", __name__)


def err(message, code=400):
    return jsonify(error=message), code


def register(app):
    """Register this blueprint plus every other clipper/*_api.py module that defines `bp`,
    so new feature modules don't need any change to app.py."""
    app.register_blueprint(bp)
    here = Path(__file__)
    for path in sorted(here.parent.glob("*_api.py")):
        if path.stem == here.stem:
            continue
        module = importlib.import_module(f"{__package__}.{path.stem}")
        if hasattr(module, "bp"):
            app.register_blueprint(module.bp)


def _rules(row):
    return camp.normalise_rules(json.loads(row["rules_json"]))


@bp.get("/campaigns")
def campaigns_page():
    return render_template("campaigns.html")


@bp.get("/api/campaigns")
def list_campaigns():
    rows = db.query(
        "SELECT c.id, c.name, c.status, c.source_name, c.rules_json, c.updated_at, "
        "(SELECT COUNT(*) FROM projects p WHERE p.campaign_id = c.id) AS projects "
        "FROM campaigns c ORDER BY c.id DESC")
    out = [{"id": r["id"], "name": r["name"], "status": r["status"], "source_name": r["source_name"],
            "summary": _rules(r)["summary"], "projects": r["projects"]} for r in rows]
    return jsonify(campaigns=out)


@bp.post("/api/campaigns")
def create_campaign():
    name = (request.form.get("name") or "").strip()[:100]
    f = request.files.get("file")
    try:
        if f and f.filename:
            text = docs.read_document(f.filename, f.stream.read(docs.MAX_BYTES + 1))
            source = f.filename
            name = name or Path(f.filename).stem[:100]
        else:
            text = docs.read_text(request.form.get("text"))
            source = "pasted text"
            name = name or "Pasted campaign"
    except docs.DocError as e:
        return err(str(e))

    st = llm.status()
    ready = st["running"] and st["installed"]
    cid = db.execute(
        "INSERT INTO campaigns (name, source_name, source_text, rules_json, status, message, error) "
        "VALUES (?,?,?,?,?,?,?)",
        (name, source, text, json.dumps(camp.EMPTY_RULES),
         "extracting" if ready else "failed",
         "Waiting to read the document" if ready else None,
         None if ready else st["message"] + " You can still fill in the rules by hand."))
    if ready:
        jobs.submit_extraction(cid)
    return jsonify(id=cid), 201


@bp.get("/api/campaigns/<int:cid>")
def get_campaign(cid):
    row = db.query_one("SELECT * FROM campaigns WHERE id=?", (cid,))
    if not row:
        return err("Campaign not found.", 404)
    return jsonify(id=row["id"], name=row["name"], status=row["status"], message=row["message"],
                   error=row["error"], source_name=row["source_name"],
                   source_chars=len(row["source_text"]), source_preview=row["source_text"][:3000],
                   rules=_rules(row))


@bp.put("/api/campaigns/<int:cid>")
def save_campaign(cid):
    row = db.query_one("SELECT * FROM campaigns WHERE id=?", (cid,))
    if not row:
        return err("Campaign not found.", 404)
    if row["status"] == "extracting":
        return err("Wait until the AI has finished reading the document.", 409)
    body = request.get_json(silent=True) or {}
    name = str(body.get("name") or "").strip()[:100] or row["name"]
    try:
        rules = camp.normalise_rules(body.get("rules"))
    except ValueError as e:
        return err(str(e))
    db.execute("UPDATE campaigns SET name=?, rules_json=?, status='ready', error=NULL, "
               "message='Saved.', updated_at=CURRENT_TIMESTAMP WHERE id=?",
               (name, json.dumps(rules), cid))
    return jsonify(ok=True, rules=rules)


@bp.post("/api/campaigns/<int:cid>/reextract")
def reextract_campaign(cid):
    row = db.query_one("SELECT status FROM campaigns WHERE id=?", (cid,))
    if not row:
        return err("Campaign not found.", 404)
    if row["status"] == "extracting":
        return err("The AI is already reading this document.", 409)
    st = llm.status()
    if not (st["running"] and st["installed"]):
        return err(st["message"], 503)
    db.execute("UPDATE campaigns SET status='extracting', message='Waiting to read the document', "
               "error=NULL WHERE id=?", (cid,))
    jobs.submit_extraction(cid)
    return jsonify(ok=True), 202


@bp.delete("/api/campaigns/<int:cid>")
def delete_campaign(cid):
    if not db.query_one("SELECT id FROM campaigns WHERE id=?", (cid,)):
        return err("Campaign not found.", 404)
    db.execute("UPDATE projects SET campaign_id=NULL WHERE campaign_id=?", (cid,))
    db.execute("DELETE FROM campaigns WHERE id=?", (cid,))
    return jsonify(ok=True)


# ---------- which campaign a project uses ----------

@bp.get("/api/projects/<int:pid>/campaign")
def get_project_campaign(pid):
    p = db.query_one("SELECT campaign_id FROM projects WHERE id=?", (pid,))
    if not p:
        return err("Project not found.", 404)
    row = None
    if p["campaign_id"]:
        row = db.query_one("SELECT * FROM campaigns WHERE id=?", (p["campaign_id"],))
    if not row:
        return jsonify(campaign_id=None, name=None, rules=None)
    return jsonify(campaign_id=row["id"], name=row["name"], rules=_rules(row))


@bp.put("/api/projects/<int:pid>/campaign")
def set_project_campaign(pid):
    if not db.query_one("SELECT id FROM projects WHERE id=?", (pid,)):
        return err("Project not found.", 404)
    cid = (request.get_json(silent=True) or {}).get("campaign_id")
    if cid in (None, ""):
        db.execute("UPDATE projects SET campaign_id=NULL WHERE id=?", (pid,))
        return jsonify(campaign_id=None)
    try:
        cid = int(cid)
    except (TypeError, ValueError):
        return err("Invalid campaign.")
    if not db.query_one("SELECT id FROM campaigns WHERE id=?", (cid,)):
        return err("Campaign not found.", 404)
    db.execute("UPDATE projects SET campaign_id=? WHERE id=?", (cid, pid))
    return jsonify(campaign_id=cid)


# ---------- compliance checks on finished clips ----------

@bp.get("/api/clips/<int:cid>/compliance")
def get_compliance(cid):
    row = db.query_one("SELECT report_json FROM clip_compliance WHERE clip_id=?", (cid,))
    return jsonify(report=json.loads(row["report_json"]) if row else None)


@bp.post("/api/clips/<int:cid>/compliance")
def run_compliance(cid):
    clip = db.query_one(
        "SELECT c.status, c.output_path, p.campaign_id FROM clips c "
        "JOIN projects p ON p.id = c.project_id WHERE c.id=?", (cid,))
    if not clip:
        return err("Clip not found.", 404)
    if clip["status"] != "done" or not clip["output_path"]:
        return err("Wait until the clip has finished rendering.", 409)
    if not clip["campaign_id"]:
        return err("This project has no campaign. Pick one first.")
    report = jobs.check_clip_compliance(cid)
    return jsonify(report=report)