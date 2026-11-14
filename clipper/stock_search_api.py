"""Script-order footage search, adapted from MoneyPrinterTurbo's material workflow.

Ollama proposes specific visual queries. Pixabay (optional free API key) supplies
real footage candidates. No automatic downloading, purchasing, or publishing.
"""
import json
import os
import re
import uuid
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request

from flask import Blueprint, jsonify, request

from . import broll_api, db, highlights, llm, video

bp = Blueprint("stock_search", __name__)
PIXABAY_ENDPOINT = "https://pixabay.com/api/videos/"
MAX_SCRIPT_CHARS = 9000


def _clip_sentences(clip):
    row = db.query_one(
        "SELECT status, result_json FROM transcripts WHERE project_id=?", (clip["project_id"],))
    if not row or row["status"] != "done" or not row["result_json"]:
        raise ValueError("Transcribe this video first.")
    sentences = highlights.build_sentences(json.loads(row["result_json"]).get("segments", []))
    return [s for s in sentences
            if s["end"] > clip["start_time"] and s["start"] < clip["end_time"]]


def _validate_shots(raw, sentences, clip):
    """Never trust model-provided text, IDs or times."""
    valid = {s["id"]: s for s in sentences}
    if not isinstance(raw, list):
        raise ValueError("Ollama returned an invalid visual plan.")
    result = []
    used = set()
    for entry in raw[:16]:
        if not isinstance(entry, dict):
            continue
        idx = entry.get("sentence_id")
        if type(idx) is not int or idx not in valid or idx in used:
            continue
        query = str(entry.get("query") or "").strip()
        # 1-5 short words, no URLs or arbitrary model instructions.
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9 ,'-]{1,75}", query):
            continue
        terms = query.split()
        if not 1 <= len(terms) <= 5:
            continue
        reason = str(entry.get("reason") or "").strip()[:160]
        if not reason:
            continue
        source = valid[idx]
        offset = max(0, source["start"] - clip["start_time"])
        result.append({
            "sentence_id": idx,
            "start": round(offset, 2),
            "end": round(min(clip["end_time"] - clip["start_time"],
                             source["end"] - clip["start_time"]), 2),
            "dialogue": source["text"][:240],
            "query": query,
            "reason": reason,
        })
        used.add(idx)
    return sorted(result, key=lambda s: s["start"])


@bp.post("/api/clips/<int:cid>/broll/shot-plan")
def generate_shot_plan(cid):
    clip = db.query_one("SELECT * FROM clips WHERE id=?", (cid,))
    if not clip:
        return jsonify(error="Clip not found."), 404
    try:
        sentences = _clip_sentences(clip)
        if not sentences:
            return jsonify(shots=[], message="No spoken dialogue in this clip.")
        context = [{"id": s["id"], "time": round(s["start"] - clip["start_time"], 1),
                    "speech": s["text"][:350]} for s in sentences]
        schema = {
            "type": "object",
            "properties": {"shots": {"type": "array", "items": {
                "type": "object",
                "properties": {
                    "sentence_id": {"type": "integer"},
                    "query": {"type": "string"},
                    "reason": {"type": "string"}
                },
                "required": ["sentence_id", "query", "reason"],
                "additionalProperties": False
            }}},
            "required": ["shots"],
            "additionalProperties": False,
        }
        system = (
            "You are a professional documentary/podcast editor planning REAL stock footage. "
            "Select up to 6 genuinely useful cutaways based on the meaning of the transcript. "
            "Follow dialogue order and use only sentence IDs supplied. "
            "Use concrete English stock-video search queries of 1-5 words describing "
            "a physically filmable subject or action. Avoid generic 'sad businessman', "
            "symbolic money animations, CGI, AI videos, faces attributed to actual speakers, "
            "stock footage pretending to be a real documented event, and invented facts. "
            "Skip abstract, emotional, or personal claims when there is no truthful "
            "documentary-style visual. Never insert footage just to fill time. "
            "Return an empty shots array if no shots would improve this clip. "
            "For each shot explain precisely why it matches the spoken content."
        )
        answer, _ = llm.chat_json(
            system,
            "Transcript, with stable sentence IDs and seconds into the clip:\n"
            + json.dumps(context, ensure_ascii=False)[:MAX_SCRIPT_CHARS],
            schema, timeout=180,
        )
        shots = _validate_shots(answer.get("shots"), sentences, clip)
        return jsonify(shots=shots, model="local-ollama",
                       message="Review search terms before sourcing any footage.",
                       applied=False)
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    except llm.LLMError as exc:
        return jsonify(error=str(exc)), 503


def _parse_pixabay(data):
    """Only return a video rendition and metadata for genuine Pixabay entries."""
    result = []
    for hit in data.get("hits", [])[:25]:
        if not isinstance(hit, dict):
            continue
        video = hit.get("videos") or {}
        choices = [v for k in ("medium", "small", "large", "tiny")
                   if isinstance((v := video.get(k)), dict) and v.get("url")]
        if not choices:
            continue
        # Medium is preferable to expensive 4K or tiny preview variants.
        choice = choices[0]
        url = choice["url"]
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname or not parsed.hostname.endswith(".pixabay.com"):
            continue
        result.append({
            "id": hit.get("id"),
            "duration": hit.get("duration"),
            "width": choice.get("width"),
            "height": choice.get("height"),
            "preview": hit.get("pageURL"),
            "creator": hit.get("user"),
            "download_url": url,
        })
    return result


@bp.get("/api/broll/stock/search")
def search_pixabay():
    query = (request.args.get("q") or "").strip()
    if not 2 <= len(query) <= 80 or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9 ,'-]*", query):
        return jsonify(error="Enter a short, plain-English footage search."), 400
    key = os.environ.get("PIXABAY_API_KEY", "").strip()
    if not key:
        return jsonify(error="Pixabay search needs a free PIXABAY_API_KEY environment variable.",
                       provider="pixabay", configured=False), 503
    url = PIXABAY_ENDPOINT + "?" + urllib.parse.urlencode({
        "key": key, "q": query, "per_page": 20, "video_type": "film"})
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "LocalVideoEditor/1.0"})
        with urllib.request.urlopen(req, timeout=25) as resp:
            data = json.loads(resp.read(2 * 1024 * 1024).decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        return jsonify(error="Pixabay search failed; retry later or upload local footage."), 502
    return jsonify(provider="pixabay", results=_parse_pixabay(data),
                   query=query, license_url="https://pixabay.com/service/license-summary/")


def _trusted_pixabay_url(url):
    parsed = urllib.parse.urlsplit(str(url))
    host = (parsed.hostname or "").lower()
    return (parsed.scheme == "https" and parsed.port in (None, 443)
            and parsed.username is None and parsed.password is None
            and (host == "pixabay.com" or host.endswith(".pixabay.com"))
            and len(url) < 2500)


class _PixabayOnlyRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _trusted_pixabay_url(newurl):
            raise ValueError("Stock media redirected away from Pixabay.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


@bp.post("/api/projects/<int:pid>/broll/import-stock")
def import_pixabay_video(pid):
    """Explicitly import one chosen Pixabay stock clip into the local B-roll library."""
    if not broll_api._project(pid):
        return jsonify(error="Project not found."), 404
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify(error="Invalid import request."), 400
    url = payload.get("url")
    if not isinstance(url, str) or not _trusted_pixabay_url(url):
        return jsonify(error="Only HTTPS Pixabay video URLs can be imported."), 400
    if not urllib.parse.urlsplit(url).path.lower().endswith(".mp4"):
        return jsonify(error="Only MP4 stock footage is supported."), 400
    description = broll_api._label(payload.get("description"))
    folder = broll_api._folder(pid)
    folder.mkdir(parents=True, exist_ok=True)
    item_id = uuid.uuid4().hex
    target = folder / (item_id + ".mp4")
    size = 0
    try:
        opener = urllib.request.build_opener(_PixabayOnlyRedirects())
        with opener.open(urllib.request.Request(url, headers={"User-Agent": "LocalVideoEditor/1.0"}),
                         timeout=40) as response, target.open("wb") as output:
            if not _trusted_pixabay_url(response.url):
                raise ValueError("Stock media came from an unexpected location.")
            length = response.headers.get("Content-Length")
            if length and int(length) > broll_api.MAX_BYTES:
                raise ValueError("Stock video exceeds 200 MB.")
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > broll_api.MAX_BYTES:
                    raise ValueError("Stock video exceeds 200 MB.")
                output.write(chunk)
        if not size:
            raise ValueError("Stock footage was empty.")
        video.probe(target)
        with broll_api._meta_lock:
            metadata = broll_api._metadata(pid)
            metadata[item_id] = {
                "name": f"Pixabay footage {item_id[:8]}.mp4",
                "description": description,
                "provider": "pixabay",
                "source_page": str(payload.get("source_page") or "")[:300],
            }
            broll_api._save_metadata(pid, metadata)
    except (OSError, ValueError, urllib.error.URLError, TimeoutError, video.VideoError) as exc:
        target.unlink(missing_ok=True)
        return jsonify(error="Could not import this Pixabay video. Try another result."), 422
    return jsonify(ok=True, id=item_id, bytes=size), 201
