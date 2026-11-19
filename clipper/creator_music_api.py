"""Creator Studio free music presets, local soundtrack synthesis and reuse.

Original algorithmic instrumental loops; no copyrighted audio bundled.
MoneyPrinterTurbo's BGM workflow selects presets or random local tracks.
Paid cloud AI music generation is intentionally not connected.
"""
import json
import math
import os
import random
import re
import shutil
import uuid
import wave
from pathlib import Path

from flask import Blueprint, jsonify, request

import config
from . import creator_render_api as creator, db

bp = Blueprint("creator_music", __name__)
RATE = 16000
PRESETS = {
    "calm": {"chords": [[220, 261.63, 329.63], [196, 246.94, 293.66],
                       [174.61, 220, 261.63], [196, 246.94, 293.66]], "pulse": 2},
    "uplifting": {"chords": [[261.63, 329.63, 392], [220, 261.63, 329.63],
                            [174.61, 220, 349.23], [196, 293.66, 392]], "pulse": 4},
    "cinematic": {"chords": [[130.81, 196, 261.63], [146.83, 220, 293.66],
                            [110, 164.81, 220], [123.47, 185, 246.94]], "pulse": 2},
    "ambient": {"chords": [[164.81, 246.94, 329.63], [146.83, 220, 293.66],
                          [130.81, 196, 261.63], [146.83, 220, 293.66]], "pulse": 1},
}
SUPPORTED = {".mp3", ".wav", ".flac", ".aac"}


def library_dir():
    return config.DATA_DIR / "creator_music_library"


def list_saved():
    directory = library_dir()
    if not directory.is_dir():
        return []
    return sorted([p.name for p in directory.iterdir()
                   if p.is_file() and not p.is_symlink()
                   and p.suffix.lower() in SUPPORTED and p.name.startswith("preset_")])


def generate_original(path, mood, seconds):
    """Generate an original pad-and-pulse instrumental WAV, without any sample library."""
    if mood not in PRESETS or type(seconds) is not int or not 5 <= seconds <= 120:
        raise ValueError("Choose a supported mood and 5–120 seconds.")
    notes = PRESETS[mood]["chords"]
    pulse_rate = PRESETS[mood]["pulse"]
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(RATE)
        for block_start in range(0, seconds * RATE, 2048):
            count = min(2048, seconds * RATE - block_start)
            buffer = bytearray()
            for i in range(count):
                tick = block_start+i
                t = tick/RATE
                chord = notes[int(t//4) % len(notes)]
                # Softly crossfade over the first/last quarter second of each chord.
                phase = t%4
                fade = min(1.0, phase*3, max(0.0, (4-phase)*3))
                base = sum(math.sin(2*math.pi*freq*t) for freq in chord)/len(chord)
                harmonic = sum(math.sin(2*math.pi*(freq*2)*t) for freq in chord)/len(chord)
                tremolo = 0.82 + 0.18*math.cos(2*math.pi*pulse_rate*t/4)
                envelope = min(1, t/0.4, max(0.0, (seconds-t)/0.6))
                sample = (0.47*base + 0.06*harmonic)*fade*tremolo*envelope
                sample = max(-0.9, min(0.9, sample))
                buffer.extend(int(sample*32767).to_bytes(2, "little", signed=True))
            wav.writeframesraw(buffer)


def apply_music(pid, source, mode):
    with creator._upload_lock:
        project = creator._project(pid)
        if not project:
            raise ValueError("Project not found.")
        if project["status"] in creator.BUSY:
            raise ValueError("Another production stage is active.")
        if not source.is_file():
            raise ValueError("Preset track is missing.")
        out_dir = creator._root(pid) / "music"
        out_dir.mkdir(parents=True, exist_ok=True)
        name = "music_" + uuid.uuid4().hex + source.suffix.lower()
        out = out_dir / name
        shutil.copyfile(source, out)
        duration = creator._probe_audio(out)
        with db.get_db() as conn:
            conn.execute("UPDATE creator_stages SET status='done', artifact_json=?, error=NULL, "
                         "updated_at=CURRENT_TIMESTAMP WHERE project_id=? AND stage='music'",
                         (json.dumps({"filename": name, "duration": duration, "mode": mode}), pid))
             creator.invalidate_render(conn, pid)
        return {"filename": name, "duration": duration, "mode": mode}


@bp.get("/api/creator/music/presets")
def presets():
    return jsonify(moods=list(PRESETS), saved=list_saved(),
                   note="AI music-generation services are not bundled; built-in moods are original local instrumental synthesis.")


@bp.post("/api/creator/projects/<int:pid>/music/generate")
def generate(pid):
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify(error="Choose an instrumental mood."), 400
    mood, seconds = body.get("mood"), body.get("seconds", 30)
    if mood not in PRESETS or type(seconds) is not int or not 5 <= seconds <= 120:
        return jsonify(error="Invalid mood or length (5–120 seconds)."), 400
    project = creator._project(pid)
    if not project:
        return jsonify(error="Project not found."), 404
    if project["status"] in creator.BUSY:
        return jsonify(error="Another production stage is active."), 409
    # Use the same project-specific music store as normal user uploads.
    file = creator._root(pid) / "music" / ("music_" + uuid.uuid4().hex + ".wav")
    try:
        generate_original(file, mood, seconds)
        duration = creator._probe_audio(file)
        with creator._upload_lock:
            if creator._project(pid)["status"] in creator.BUSY:
                file.unlink(missing_ok=True)
                return jsonify(error="Another production stage started."), 409
            with db.get_db() as conn:
                conn.execute("UPDATE creator_stages SET status='done', artifact_json=?, error=NULL, "
                             "updated_at=CURRENT_TIMESTAMP WHERE project_id=? AND stage='music'",
                             (json.dumps({"filename": file.name, "duration": duration,
                                          "mode": "generated-" + mood}), pid))
                 creator.invalidate_render(conn, pid)
        return jsonify(ok=True, mode="generated-" + mood, duration=duration), 201
    except Exception:
        file.unlink(missing_ok=True)
        return jsonify(error="Local soundtrack generation failed."), 500


@bp.post("/api/creator/projects/<int:pid>/music/save-preset")
def save_preset(pid):
    item = creator._music(pid)
    if not item:
        return jsonify(error="Upload or generate a track first."), 409
    directory = library_dir()
    directory.mkdir(parents=True, exist_ok=True)
    name = "preset_" + uuid.uuid4().hex + item["file"].suffix.lower()
    shutil.copyfile(item["file"], directory / name)
    return jsonify(ok=True, filename=name), 201


@bp.post("/api/creator/projects/<int:pid>/music/select-preset")
def select_preset(pid):
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return jsonify(error="Invalid selection."), 400
    name = body.get("filename")
    choices = list_saved()
    if name == "random":
        if not choices:
            return jsonify(error="Save at least one track to the preset library first."), 409
        name = random.choice(choices)
    if not isinstance(name, str) or name not in choices:
        return jsonify(error="Unknown library track."), 400
    try:
        result = apply_music(pid, library_dir() / name, "preset")
        return jsonify(ok=True, music=result), 201
    except ValueError as exc:
        return jsonify(error=str(exc)), 409
