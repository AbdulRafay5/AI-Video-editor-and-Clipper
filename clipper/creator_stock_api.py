"""Free automatic Creator Studio stock sourcing.

Uses MoneyPrinterTurbo's material-selection strategy (duration filtering,
orientation preference, smallest adequate rendition) with the editor's existing
Pixabay search cache and trusted-download rules. No paid provider, browsing
automation, or changes to legacy clip/B-roll APIs.
"""
import json
import logging
import os
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor

from flask import Blueprint, jsonify, request

from . import creator_render_api as creator, db, stock_cache, stock_search_api as stock, video

bp = Blueprint("creator_stock", __name__)
log = logging.getLogger("clipper.creator_stock")
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="creator-stock")
MAX_SLOTS_PER_BATCH = 6
MAX_DOWNLOAD_BYTES = 200 * 1024 * 1024
QUERY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ,'-]{1,79}$")


def query_for(slot, script):
    """Deterministic, filmable search hints; users can edit visual_idea in script."""
    if slot == "hook":
        prompt = script["scenes"][0]["visual_idea"]
    elif slot == "closing":
        prompt = script["scenes"][-1]["visual_idea"]
    else:
        index = int(slot.split("_")[1])
        prompt = script["scenes"][index]["visual_idea"]
    words = re.findall(r"[A-Za-z0-9]+", prompt)
    # English physical subjects get higher search quality than long invented prose.
    words = [w for w in words if w.lower() not in
             {"the", "a", "an", "of", "and", "for", "with", "showing", "shot",
              "cinematic", "footage", "scene", "video", "realistic"}]
    return " ".join(words[:5])[:75]


def _search(query):
    key = os.environ.get("PIXABAY_API_KEY", "").strip()
    if not key:
        raise ValueError("Free Pixabay API key not configured (PIXABAY_API_KEY).")
    if not QUERY_PATTERN.fullmatch(query):
        raise ValueError("Visual idea doesn't produce a valid stock-video search.")
    filters = {"video_type": "film", "per_page": 30}
    def remote():
        url = stock.PIXABAY_ENDPOINT + "?" + urllib.parse.urlencode(
            {"key": key, "q": query, **filters})
        req = urllib.request.Request(url, headers={"User-Agent": "LocalVideoEditor/1.0"})
        with urllib.request.urlopen(req, timeout=25) as response:
            data = json.loads(response.read(2 * 1024 * 1024).decode("utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("hits"), list):
            raise ValueError("Unexpected Pixabay search response.")
        return _candidates(data)
    return stock_cache.cached_search("pixabay_creator", query, filters, remote)[0]


def _candidates(data):
    """Adapt MoneyPrinterTurbo's smallest-suitable-rendition rule.

    Portrait first, then landscape for FFmpeg's safe centre crop.
    Square also allowed. Never invent a visual relevance score.
    """
    if not isinstance(data, dict) or not isinstance(data.get("hits"), list):
        return []
    results = []
    for hit in data["hits"][:50]:
        if not isinstance(hit, dict):
            continue
        duration = hit.get("duration")
        if type(duration) not in (int, float) or not 1 <= duration <= 3600:
            continue
        variants = hit.get("videos")
        if not isinstance(variants, dict):
            continue
        choices = []
        for variant in variants.values():
            if not isinstance(variant, dict):
                continue
            try:
                w = int(variant.get("width"))
                h = int(variant.get("height"))
            except (ValueError, TypeError, OverflowError):
                continue
            url = variant.get("url")
            if w < 360 or h < 360 or not isinstance(url, str) or not stock._trusted_pixabay_url(url):
                continue
            if not urllib.parse.urlsplit(url).path.lower().endswith(".mp4"):
                continue
            if w * h > 3840 * 2160:
                continue
            choices.append((w * h, w, h, url))
        if not choices:
            continue
        portrait = [x for x in choices if x[2] > x[1]]
        selected = min(portrait or choices, key=lambda item: item[0])
        results.append({
            "id": hit.get("id"), "duration": duration,
            "width": selected[1], "height": selected[2],
            "download_url": selected[3],
            "source_page": str(hit.get("pageURL") or "")[:300],
            "creator": str(hit.get("user") or "")[:100],
            "portrait": selected[2] > selected[1],
        })
    return results


def _download(url, destination):
    if not stock._trusted_pixabay_url(url):
        raise ValueError("Stock source failed URL validation.")
    opener = urllib.request.build_opener(stock._PixabayOnlyRedirects(),
                                         urllib.request.ProxyHandler({}))
    req = urllib.request.Request(url, headers={"User-Agent": "LocalVideoEditor/1.0"})
    written = 0
    try:
        with opener.open(req, timeout=40) as response, destination.open("wb") as out:
            if not stock._trusted_pixabay_url(response.url):
                raise ValueError("Unexpected download origin.")
            length = response.headers.get("Content-Length")
            if length and int(length) > MAX_DOWNLOAD_BYTES:
                raise ValueError("Footage exceeds 200MB.")
            while chunk := response.read(1024 * 1024):
                written += len(chunk)
                if written > MAX_DOWNLOAD_BYTES:
                    raise ValueError("Footage exceeds 200MB.")
                out.write(chunk)
        if written < 1024:
            raise ValueError("Empty stock footage.")
        video.probe(destination)
    except Exception:
        destination.unlink(missing_ok=True)
        raise


def _run(pid, requested):
    errors = {}
    applied = {}
    downloaded = []
    try:
        project = creator._project(pid)
        script = json.loads(project["script_json"])
        clips = creator._visuals(pid)
        seen_urls = set()
        all_slots = creator._slots(script)
        for slot in requested:
            if clips.get(slot):
                continue
            query = query_for(slot, script)
            try:
                candidates = _search(query)
                chosen = next((c for c in candidates if c["download_url"] not in seen_urls), None)
                if chosen is None:
                    errors[slot] = "No matching free footage found; upload a local video."
                    continue
                seen_urls.add(chosen["download_url"])
                folder = creator._root(pid) / "visuals"
                folder.mkdir(parents=True, exist_ok=True)
                filename = "clip_" + uuid.uuid4().hex + ".mp4"
                target = folder / filename
                _download(chosen["download_url"], target)
                clips[slot] = filename
                downloaded.append(target)
                applied[slot] = {
                    "filename": filename, "provider": "pixabay",
                    "source_page": chosen["source_page"],
                    "creator": chosen["creator"], "query": query,
                    "asset_id": chosen["id"],
                }
            except (ValueError, OSError, urllib.error.URLError, TimeoutError, video.VideoError):
                errors[slot] = "Search or download failed; try another idea or upload footage."
                log.warning("Free footage unavailable for Creator %s slot %s", pid, slot)
        with db.get_db() as conn:
            row = conn.execute("SELECT artifact_json FROM creator_stages "
                               "WHERE project_id=? AND stage='visuals'", (pid,)).fetchone()
            old = json.loads(row["artifact_json"]) if row and row["artifact_json"] else {}
            current = old.get("clips", {})
            # Preserve manual uploads and any sources already saved.
            current.update({slot: filename for slot, filename in clips.items() if slot not in current})
            sources = old.get("sources", {})
            sources.update(applied)
            stage = "done" if all(current.get(s) for s in all_slots) else "pending"
            conn.execute("UPDATE creator_stages SET status=?, artifact_json=?, error=NULL, "
                         "updated_at=CURRENT_TIMESTAMP WHERE project_id=? AND stage='visuals'",
                         (stage, json.dumps({"clips": current, "sources": sources}), pid))
            if applied:
                conn.execute("UPDATE creator_stages SET status='pending', artifact_json=NULL, error=NULL "
                             "WHERE project_id=? AND stage='render'", (pid,))
            conn.execute("UPDATE creator_projects SET status=?, error=?, "
                         "updated_at=CURRENT_TIMESTAMP WHERE id=?",
                         ("narration_ready" if bool(db.query_one("SELECT 1 FROM creator_stages WHERE project_id=? AND stage='narration' AND status='done'", (pid,))) else "script_ready",
                          ("Some shots couldn't be sourced; see the footage report." if errors else None), pid))
            conn.execute("INSERT OR REPLACE INTO creator_stock_jobs(project_id,status,report_json) "
                         "VALUES (?,?,?)",
                         (pid, "done", json.dumps({"applied": applied, "errors": errors})))
    except Exception:
        log.exception("Creator free stock sourcing failed")
        with db.get_db() as conn:
            conn.execute("UPDATE creator_projects SET status='script_ready', "
                         "error='Stock sourcing failed; retry missing slots.' WHERE id=?", (pid,))
            conn.execute("INSERT OR REPLACE INTO creator_stock_jobs(project_id,status,report_json) "
                         "VALUES (?,?,?)", (pid, "failed", json.dumps({"errors": {"general": "Stock sourcing failed."}})))


@bp.post("/api/creator/projects/<int:pid>/stock/auto-fill")
def auto_fill(pid):
    body = request.get_json(silent=True)
    if body is None:
        body = {}
    if not isinstance(body, dict):
        return jsonify(error="Invalid request."), 400
    if body.get("approve_download") is not True:
        return jsonify(error="Confirm downloading free footage before starting."), 400
    if not os.environ.get("PIXABAY_API_KEY", "").strip():
        return jsonify(error="Add a free PIXABAY_API_KEY first."), 503
    with creator._upload_lock:
        with db.get_db() as conn:
            project = conn.execute("SELECT status, script_json FROM creator_projects "
                                   "WHERE id=?", (pid,)).fetchone()
            if not project:
                return jsonify(error="Project not found."), 404
            if project["status"] in creator.BUSY or project["status"] == "sourcing_visuals":
                return jsonify(error="Another production stage is running."), 409
            if not project["script_json"]:
                return jsonify(error="Save a script first."), 409
            script = json.loads(project["script_json"])
            available = [s for s in creator._slots(script) if not creator._visuals(pid).get(s)]
            if not available:
                return jsonify(error="All scenes already have footage."), 409
            selected = available[:MAX_SLOTS_PER_BATCH]
            conn.execute("UPDATE creator_projects SET status='sourcing_visuals', error=NULL, "
                         "updated_at=CURRENT_TIMESTAMP WHERE id=?", (pid,))
            conn.execute("INSERT OR REPLACE INTO creator_stock_jobs(project_id,status,report_json) "
                         "VALUES (?,?,?)", (pid, "running", json.dumps({"requested": selected})))
    _executor.submit(_run, pid, selected)
    return jsonify(ok=True, status="sourcing_visuals", requested=selected,
                   remaining=max(0, len(available) - len(selected))), 202


@bp.get("/api/creator/projects/<int:pid>/stock")
def stock_status(pid):
    if not creator._project(pid):
        return jsonify(error="Project not found."), 404
    row = db.query_one("SELECT status, report_json FROM creator_stock_jobs WHERE project_id=?", (pid,))
    return jsonify(status=row["status"] if row else "pending",
                   report=json.loads(row["report_json"]) if row and row["report_json"] else None)
