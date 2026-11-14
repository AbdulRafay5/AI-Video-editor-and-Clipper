"""Free local Whisper word-alignment for Creator Studio narration.

Uses the existing faster-whisper dependency and the same word_timestamps=True
approach as MoneyPrinterTurbo. Runs on CPU int8 to avoid requiring more GPU VRAM.
Downloads a public model on first use only when the user explicitly starts it.
"""
import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from flask import Blueprint, jsonify

from . import creator_render_api as creator, db

bp = Blueprint("creator_alignment", __name__)
log = logging.getLogger("clipper.creator_alignment")
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="creator-whisper")
_model_lock = threading.RLock()
_busy = ("generating_script", "generating_narration", "rendering", "sourcing_visuals", "aligning_captions", "generating_music", "generating_images")


def _voice(pid):
    row = db.query_one("SELECT status, artifact_json FROM creator_stages "
                       "WHERE project_id=? AND stage='narration'", (pid,))
    if not row or row["status"] != "done" or not row["artifact_json"]:
        raise ValueError("Generate narration before aligning subtitles.")
    artifact = json.loads(row["artifact_json"])
    run_id = artifact.get("run_id")
    if not isinstance(run_id, str) or len(run_id) != 32 or any(c not in "0123456789abcdef" for c in run_id):
        raise ValueError("Invalid narration reference.")
    audio = creator._root(pid) / "narration" / run_id / "full.wav"
    if not audio.is_file():
        raise ValueError("Narration WAV is missing.")
    return audio, run_id, float(artifact["duration"])


def _align(pid, wav, run_id, duration):
    try:
        from faster_whisper import WhisperModel
        with _model_lock:
            model = WhisperModel("tiny.en", device="cpu", compute_type="int8")
            chunks, _ = model.transcribe(
                str(wav), beam_size=5, word_timestamps=True,
                vad_filter=True, vad_parameters={"min_silence_duration_ms": 500})
            words = []
            for chunk in chunks:
                for item in (chunk.words or []):
                    word = str(item.word).strip()
                    if not word:
                        continue
                    start, end = float(item.start), float(item.end)
                    if 0 <= start < end <= duration + 0.5:
                        words.append({"word": word[:100], "start": round(start, 3),
                                      "end": round(min(end, duration), 3)})
            if not words:
                raise ValueError("Whisper found no spoken words in the narration.")
            with db.get_db() as conn:
                row = conn.execute("SELECT artifact_json FROM creator_stages "
                                   "WHERE project_id=? AND stage='captions'", (pid,)).fetchone()
                saved = json.loads(row["artifact_json"]) if row and row["artifact_json"] else {}
                saved.update({"aligned_words": words, "narration_run_id": run_id})
                saved.setdefault("captions", True)
                saved.setdefault("fade_edges", True)
                conn.execute("UPDATE creator_stages SET status='done', artifact_json=?, error=NULL, "
                             "updated_at=CURRENT_TIMESTAMP WHERE project_id=? AND stage='captions'",
                             (json.dumps(saved, ensure_ascii=False), pid))
                conn.execute("UPDATE creator_stages SET status='pending', artifact_json=NULL, error=NULL "
                             "WHERE project_id=? AND stage='render'", (pid,))
                conn.execute("UPDATE creator_projects SET status='narration_ready', error=NULL, "
                             "updated_at=CURRENT_TIMESTAMP WHERE id=?", (pid,))
    except Exception as exc:
        log.exception("Whisper alignment failed for Creator %s", pid)
        detail = str(exc)[:250] if isinstance(exc, (ValueError, ImportError)) else "Whisper alignment failed; check local logs."
        with db.get_db() as conn:
            conn.execute("UPDATE creator_projects SET status='narration_ready', error=?, "
                         "updated_at=CURRENT_TIMESTAMP WHERE id=?", (detail, pid))
            conn.execute("UPDATE creator_stages SET status='failed', error=?, "
                         "updated_at=CURRENT_TIMESTAMP WHERE project_id=? AND stage='captions'", (detail, pid))


@bp.post("/api/creator/projects/<int:pid>/captions/align")
def align(pid):
    with db.get_db() as conn:
        row = conn.execute("SELECT status FROM creator_projects WHERE id=?", (pid,)).fetchone()
        if not row:
            return jsonify(error="Project not found."), 404
        if row["status"] in _busy:
            return jsonify(error="Wait for the current production stage."), 409
        try:
            wav, run_id, duration = _voice(pid)
        except ValueError as exc:
            return jsonify(error=str(exc)), 409
        conn.execute("UPDATE creator_projects SET status='aligning_captions', error=NULL, "
                     "updated_at=CURRENT_TIMESTAMP WHERE id=?", (pid,))
        conn.execute("UPDATE creator_stages SET status='running', error=NULL, "
                     "updated_at=CURRENT_TIMESTAMP WHERE project_id=? AND stage='captions'", (pid,))
    _executor.submit(_align, pid, wav, run_id, duration)
    return jsonify(ok=True, status="aligning_captions", model="tiny.en", device="cpu"), 202
