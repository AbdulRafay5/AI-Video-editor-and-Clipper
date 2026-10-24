"""Export finished clips as one zip, and open the output folder."""
import json
import logging
import os
import re
import time
import zipfile
from pathlib import Path

from flask import Blueprint, jsonify, request, send_file

import config
from . import captions as cap
from . import db

log = logging.getLogger("clipper.export_api")
bp = Blueprint("export", __name__)

EXPORT_DIR = config.DATA_DIR / "exports"
EXPORT_DIR.mkdir(parents=True, exist_ok=True)

STATUS_LABEL = {"pass": "Verified", "review": "Check by hand", "fail": "Does not comply"}


def err(message, code=400):
    return jsonify(error=message), code


def _safe(name):
    cleaned = re.sub(r"[^A-Za-z0-9._ -]+", "_", name or "").strip(" ._")
    return cleaned[:80] or "clip"


def _remove_old_exports(max_age_seconds=24 * 3600):
    for old in EXPORT_DIR.glob("*.zip"):
        try:
            if time.time() - old.stat().st_mtime > max_age_seconds:
                old.unlink()
        except OSError:
            pass


def _summary_block(name, report):
    lines = [f"Clip: {name}",
             f"Campaign: {report.get('campaign', '')}",
             f"Result: {report.get('overall', '')}"]
    for c in report.get("checks", []):
        lines.append(f"  [{STATUS_LABEL.get(c['status'], c['status'])}] {c['rule']}: {c['detail']}")
    return "\n".join(lines)


@bp.get("/api/projects/<int:pid>/export.zip")
def export_zip(pid):
    p = db.query_one("SELECT * FROM projects WHERE id=?", (pid,))
    if not p:
        return err("Project not found.", 404)
    wanted = {int(x) for x in re.findall(r"\d+", request.args.get("ids", ""))}
    rows = db.query("SELECT * FROM clips WHERE project_id=? AND status='done' ORDER BY start_time, id", (pid,))
    if wanted:
        rows = [r for r in rows if r["id"] in wanted]
    if not rows:
        return err("There are no finished clips to export.")

    cap_row = db.query_one("SELECT captions_json, style_json FROM captions WHERE project_id=?", (pid,))
    captions = json.loads(cap_row["captions_json"]) if cap_row else None
    line_chars = cap.normalise_style(json.loads(cap_row["style_json"]))["line_chars"] if cap_row else 24

    _remove_old_exports()
    zip_path = EXPORT_DIR / f"{_safe(p['name'])}_{time.strftime('%Y%m%d_%H%M%S')}.zip"
    summary = ["Compliance summary",
               "The app only verifies what it can; everything marked 'Check by hand' needs a person.", ""]
    exported = 0
    try:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_STORED) as z:   # mp4 is already compressed
            for i, c in enumerate(rows, 1):
                base = f"{i:02d}_{_safe(c['name'])}"
                path = Path(c["output_path"] or "")
                if not path.is_file():
                    summary.append(f"Clip: {c['name']}\n  The video file is missing, so it was skipped.\n")
                    continue
                z.write(path, f"clips/{base}.mp4")
                exported += 1
                if captions:
                    srt = cap.to_srt(captions, line_chars=line_chars,
                                     start=c["start_time"], end=c["end_time"])
                    if srt.strip():
                        z.writestr(f"subtitles/{base}.srt", srt)
                row = db.query_one("SELECT report_json FROM clip_compliance WHERE clip_id=?", (c["id"],))
                if row:
                    report = json.loads(row["report_json"])
                    summary.append(_summary_block(c["name"], report) + "\n")
                    if report.get("post_text"):
                        z.writestr(f"post_text/{base}.txt", report["post_text"])
            z.writestr("compliance_summary.txt", "\n".join(summary))
    except OSError as e:
        log.exception("Export failed")
        zip_path.unlink(missing_ok=True)
        return err(f"Could not create the zip ({e}). Check that there is enough free disk space.", 500)
    if not exported:
        zip_path.unlink(missing_ok=True)
        return err("None of the selected clips have a video file on disk.", 404)
    return send_file(zip_path, mimetype="application/zip", as_attachment=True, download_name=zip_path.name)


@bp.post("/api/projects/<int:pid>/open-folder")
def open_folder(pid):
    folder = config.OUTPUT_DIR / f"project_{pid}"
    if not folder.is_dir():
        return err("There are no clips for this project yet.", 404)
    try:
        os.startfile(str(folder))   # Windows only; the app only listens on this computer
    except AttributeError:
        return err("Opening folders is only supported on Windows.", 501)
    except OSError as e:
        return err(f"Could not open the folder: {e}", 500)
    return jsonify(ok=True, path=str(folder))