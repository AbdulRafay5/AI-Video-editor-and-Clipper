"""Conservative, local transcript-to-B-roll suggestions.

This creates *reviewable edit plans*, never silently changes rendered clips.
No network or paid AI service is used.
"""
import json
import re

from flask import Blueprint, jsonify

from . import broll_api, db, highlights

bp = Blueprint("broll_suggestions", __name__)
STOP = frozenset(("the a an and or but of for with from on in to at is are was were be been "
                  "this that those these there their they them he she his her we our you your i me "
                  "have has had do does did not no so as by it its about what where when who how "
                  "why like just really very one two into over out up down more less video clip "
                  "footage image photo shot broll scene").split())


def keywords(text):
    return {w for w in re.findall(r"[a-z0-9]{3,}", text.lower()) if w not in STOP}


def build_suggestions(sentences, assets, clip_start, clip_end, limit=6):
    """Return non-overlapping suggestions with timestamps relative to the exported clip.

    A title/description must share meaningful words with spoken dialogue;
    never fill gaps with arbitrarily chosen footage.
    """
    candidates = []
    for asset in assets:
        words = keywords(asset.get("name", "") + " " + asset.get("description", ""))
        if not words:
            continue
        for sentence in sentences:
            start, end = float(sentence["start"]), float(sentence["end"])
            if end <= clip_start + 1 or start >= clip_end - 0.5:
                continue
            found = sorted(words & keywords(sentence.get("text", "")))
            if not found:
                continue
            position = max(1.0, start - clip_start)
            duration = min(3.5, clip_end - clip_start - 0.4 - position)
            if duration < 1.0:
                continue
            # Prefer two or more matching words; a lone word is a weak suggestion.
            candidates.append({
                "asset_id": asset["id"],
                "asset_name": asset["name"],
                "start": round(position, 2),
                "end": round(position + duration, 2),
                "matched_terms": found[:5],
                "confidence": "medium" if len(found) >= 2 else "low",
                "_score": len(found),
            })
    chosen = []
    used_assets = set()
    for item in sorted(candidates, key=lambda v: (-v["_score"], v["start"], v["asset_id"])):
        if item["asset_id"] in used_assets:
            continue
        if any(item["start"] < old["end"] and item["end"] > old["start"] for old in chosen):
            continue
        chosen.append(item)
        used_assets.add(item["asset_id"])
        if len(chosen) == limit:
            break
    for item in chosen:
        item.pop("_score")
    return sorted(chosen, key=lambda v: v["start"])


@bp.get("/api/clips/<int:cid>/broll/suggestions")
def broll_suggestions(cid):
    clip = db.query_one("SELECT * FROM clips WHERE id=?", (cid,))
    if not clip:
        return jsonify(error="Clip not found."), 404
    pid = clip["project_id"]
    with broll_api._meta_lock:
        metadata = broll_api._metadata(pid)
    folder = broll_api._folder(pid)
    assets = []
    if folder.is_dir():
        for f in folder.iterdir():
            if not f.is_file() or f.suffix not in broll_api.ALLOWED or len(f.stem) != 32:
                continue
            item = metadata.get(f.stem, {})
            assets.append({"id": f.stem, "name": item.get("name", f.name),
                           "description": item.get("description", "")})
    if not assets:
        return jsonify(suggestions=[], message="Add some B-roll footage first.")
    transcript = db.query_one("SELECT result_json, status FROM transcripts WHERE project_id=?", (pid,))
    if not transcript or transcript["status"] != "done" or not transcript["result_json"]:
        return jsonify(suggestions=[], message="Transcribe this project before suggesting B-roll.")
    segments = json.loads(transcript["result_json"]).get("segments", [])
    sentences = highlights.build_sentences(segments)
    suggestions = build_suggestions(sentences, assets, clip["start_time"], clip["end_time"])
    return jsonify(
        suggestions=suggestions,
        message=("Review these potential cutaways. No video has been modified."
                 if suggestions else
                 "No relevant matches found. Add descriptive footage names or descriptions."),
        method="local-keyword-matching",
        applied=False,
    )
