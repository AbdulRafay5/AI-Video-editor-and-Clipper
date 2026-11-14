"""Optional text-to-video generation using a separately configured provider.

The existing editor remains entirely local and free by default.
One explicit user request authorizes at most one chargeable generation submission.
"""
import json
import os
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid
from flask import Blueprint, jsonify, request

bp = Blueprint("generated_video", __name__)
BASE = "https://api.wavespeed.ai/api/v3"
MODEL = "bytedance/seedance-2.0-fast/text-to-video"
_lock = threading.Lock()
_jobs = {}


def _headers():
    key = os.environ.get("WAVESPEED_API_KEY", "").strip()
    if not key:
        raise ValueError("WaveSpeed is not configured. Paid generation is disabled; no API request was sent.")
    return {"Authorization": "Bearer " + key, "Content-Type": "application/json"}


def _read_json(url, headers, payload=None):
    req = urllib.request.Request(
        url, headers=headers, method="POST" if payload is not None else "GET",
        data=json.dumps(payload).encode("utf-8") if payload is not None else None)
    with urllib.request.urlopen(req, timeout=60) as response:
        return json.loads(response.read(2 * 1024 * 1024).decode("utf-8"))


def _valid_options(body):
    if not isinstance(body, dict):
        raise ValueError("Invalid generation request.")
    prompt = body.get("prompt")
    duration = body.get("duration")
    aspect = body.get("aspect_ratio")
    if not isinstance(prompt, str) or not 12 <= len(prompt.strip()) <= 1200:
        raise ValueError("Describe the scene in 12–1200 characters.")
    if type(duration) is not int or not 4 <= duration <= 15:
        raise ValueError("Choose a duration of 4–15 seconds.")
    if aspect not in ("9:16", "16:9", "1:1"):
        raise ValueError("Select a supported aspect ratio.")
    if body.get("approve_paid_generation") is not True:
        raise ValueError("Explicit payment approval is required for each generated video.")
    return {"prompt": prompt.strip(), "duration": duration, "aspect_ratio": aspect}


@bp.get("/api/video-generation/capabilities")
def capabilities():
    return jsonify(
        provider="wavespeed", model=MODEL,
        configured=bool(os.environ.get("WAVESPEED_API_KEY", "").strip()),
        pricing="Provider charges may apply; check your WaveSpeed account before approval.",
        paid=True, automatic=False, free_local_features_unaffected=True,
        durations={"min": 4, "max": 15}, aspect_ratios=["9:16", "16:9", "1:1"])


@bp.post("/api/video-generation/jobs")
def submit():
    try:
        options = _valid_options(request.get_json(silent=True))
        headers = _headers()
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    # Never repeat a potentially charged POST on timeout, network or server error.
    # The remote provider might have accepted it even when the response was lost.
    try:
        result = _read_json(BASE + "/" + MODEL, headers, options)
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        return jsonify(error=("Submission status unknown. Check the WaveSpeed dashboard "
                              "before trying again; a generation may have been charged.")), 502
    data = result.get("data") if isinstance(result, dict) else None
    prediction_id = str(data.get("id") or "") if isinstance(data, dict) else ""
    if result.get("code") != 200 or not prediction_id or not re.fullmatch(r"[A-Za-z0-9_-]{1,150}", prediction_id):
        return jsonify(error="Provider rejected the request or did not return a usable task ID. Check its dashboard."), 502
    job_id = uuid.uuid4().hex
    with _lock:
        _jobs[job_id] = prediction_id
    return jsonify(job_id=job_id, provider_job_id=prediction_id,
                   status="submitted", charged_request=True), 202


@bp.get("/api/video-generation/jobs/<job_id>")
def status(job_id):
    with _lock:
        prediction_id = _jobs.get(job_id)
    if not prediction_id:
        return jsonify(error="Unknown job. If the app restarted, check the WaveSpeed dashboard."), 404
    try:
        result = _read_json(BASE + "/predictions/" + prediction_id + "/result", _headers())
    except (urllib.error.URLError, TimeoutError, ValueError):
        return jsonify(error="Provider status unavailable; the job may still be running.",
                       provider_job_id=prediction_id), 503
    data = result.get("data") if isinstance(result, dict) else None
    if not isinstance(data, dict):
        return jsonify(error="Unexpected response from video provider.",
                       provider_job_id=prediction_id), 502
    state = str(data.get("status") or "unknown").lower()
    urls = data.get("outputs")
    outputs = [u for u in urls if isinstance(u, str) and
               urllib.parse.urlsplit(u).scheme == "https"] if isinstance(urls, list) else []
    return jsonify(status=state, outputs=outputs[:4] if state == "completed" else [],
                   provider_job_id=prediction_id)
