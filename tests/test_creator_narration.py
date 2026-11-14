"""Narration stage API tests using local WAV fixtures, no TTS provider or Internet."""
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from clipper import creator_api, creator_narration_api as narration, db, local_narration

SCRIPT = {
    "title": "A short documentary",
    "hook": "What made this family business grow?",
    "scenes": [
        {"narration": "The owners learned how to serve customers well.",
         "visual_idea": "Coffee shop counter"},
        {"narration": "People recommended the store to their friends.",
         "visual_idea": "Customers entering store"},
    ],
    "closing": "Good service helped the business grow.",
}


def make_wav(path, duration=0.2):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(16000)
        f.writeframes(b"\0\0" * int(16000 * duration))
    return local_narration.wav_duration(path)


class NarrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(db.config, "DB_PATH", Path(self.tmp.name) / "db.sqlite")
        self.data_patch = patch.object(narration.config, "DATA_DIR", Path(self.tmp.name))
        self.db_patch.start()
        self.data_patch.start()
        db.init_db()
        app = Flask(__name__)
        app.register_blueprint(creator_api.bp)
        app.register_blueprint(narration.bp)
        self.client = app.test_client()
        r = self.client.post("/api/creator/projects", json={
            "topic": "Growing a local coffee business",
            "target_seconds": 60,
        })
        self.assertEqual(r.status_code, 201)
        self.pid = r.get_json()["project"]["id"]

    def tearDown(self):
        self.data_patch.stop()
        self.db_patch.stop()
        self.tmp.cleanup()

    def test_cannot_generate_without_script(self):
        with patch.object(local_narration, "select_backend", return_value={
            "name": "test", "ready": True, "voice": "fake",
            "description": "Test voice"}):
            r = self.client.post(f"/api/creator/projects/{self.pid}/narration/generate")
        self.assertEqual(r.status_code, 409)

    def test_free_engine_not_available_returns_error(self):
        with patch.object(local_narration, "backend", return_value={
            "name": "none", "ready": False, "voice": None,
            "description": "Install Piper"}):
            r = self.client.post(f"/api/creator/projects/{self.pid}/narration/generate")
        self.assertEqual(r.status_code, 503)

    def test_builds_per_scene_audio_and_timeline(self):
        self.client.put(f"/api/creator/projects/{self.pid}/script", json=SCRIPT)
        def fake_speak(text, target, engine):
            return make_wav(target)
        def fake_join(inputs, output):
            return make_wav(output, duration=0.8)
        with patch.object(local_narration, "synthesize", side_effect=fake_speak) as synth, \
             patch.object(local_narration, "combine", side_effect=fake_join) as join:
            narration._run(self.pid, SCRIPT, {"name": "fake", "voice": "test"})
        r = self.client.get(f"/api/creator/projects/{self.pid}/narration")
        self.assertEqual(r.status_code, 200)
        data = r.get_json()["narration"]
        self.assertEqual(data["status"], "done")
        self.assertEqual(len(data["artifact"]["segments"]), 4)
        self.assertEqual([s["kind"] for s in data["artifact"]["segments"]],
                         ["hook", "scene", "scene", "closing"])
        self.assertEqual(synth.call_count, 4)
        join.assert_called_once()
        wav = self.client.get(f"/api/creator/projects/{self.pid}/narration/audio")
        self.assertEqual(wav.status_code, 200)
        self.assertTrue(wav.data.startswith(b"RIFF"))
        self.assertEqual(self.client.get(
            f"/api/creator/projects/{self.pid}/narration/audio?segment=1").status_code, 200)
        self.assertEqual(self.client.get(
            f"/api/creator/projects/{self.pid}/narration/audio?segment=999").status_code, 404)

    def test_edits_disallowed_during_running_voice_job(self):
        self.client.put(f"/api/creator/projects/{self.pid}/script", json=SCRIPT)
        db.execute("UPDATE creator_projects SET status='generating_narration' WHERE id=?", (self.pid,))
        self.assertEqual(
            self.client.put(f"/api/creator/projects/{self.pid}/script", json=SCRIPT).status_code, 409)
        self.assertEqual(self.client.delete(f"/api/creator/projects/{self.pid}").status_code, 409)

    def test_narration_restart_recovery(self):
        self.client.put(f"/api/creator/projects/{self.pid}/script", json=SCRIPT)
        db.execute("UPDATE creator_projects SET status='generating_narration' WHERE id=?", (self.pid,))
        db.execute("UPDATE creator_stages SET status='running' "
                   "WHERE project_id=? AND stage='narration'", (self.pid,))
        db.init_db()
        self.assertEqual(self.client.get(
            f"/api/creator/projects/{self.pid}/narration").get_json()["narration"]["status"], "failed")
        self.assertEqual(self.client.get(
            f"/api/creator/projects/{self.pid}").get_json()["project"]["status"], "script_ready")


if __name__ == "__main__":
    unittest.main()
