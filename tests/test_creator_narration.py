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

    def test_paced_narration_is_recorded_and_uses_measured_wav_lengths(self):
        self.client.put(f"/api/creator/projects/{self.pid}/script",json=SCRIPT)
        selected={"name":"kokoro","voice":"af_heart","ready":True,"pace":0.8}
        def synthesize(text,target,engine):
            self.assertEqual(engine["pace"],0.8)
            return make_wav(target,0.3)
        with patch.object(local_narration,"synthesize",side_effect=synthesize), \
             patch.object(local_narration,"combine",
                          side_effect=lambda files,out:make_wav(out,1.2)):
            narration._run(self.pid,SCRIPT,selected)
        saved=self.client.get(f"/api/creator/projects/{self.pid}/narration").get_json()
        artifact=saved["narration"]["artifact"]
        self.assertEqual(artifact["pace"],0.8)
        self.assertAlmostEqual(artifact["segments"][1]["start"],0.3,places=2)
        self.assertAlmostEqual(artifact["segments"][2]["start"],0.6,places=2)

    def test_generation_rejects_unsupported_pace_without_starting(self):
        self.client.put(f"/api/creator/projects/{self.pid}/script",json=SCRIPT)
        selected={"name":"windows-sapi","voice":"Windows default","ready":True}
        with patch.object(local_narration,"select_backend",return_value=selected), \
             patch.object(narration._executor,"submit") as submit:
            response=self.client.post(f"/api/creator/projects/{self.pid}/narration/generate",
                                      json={"engine":"windows-sapi","voice":"Windows default",
                                            "pace":1.2})
        self.assertEqual(response.status_code,503)
        submit.assert_not_called()

    def test_edits_disallowed_during_running_voice_job(self):
        self.client.put(f"/api/creator/projects/{self.pid}/script", json=SCRIPT)
        db.execute("UPDATE creator_projects SET status='generating_narration' WHERE id=?", (self.pid,))
        self.assertEqual(
            self.client.put(f"/api/creator/projects/{self.pid}/script", json=SCRIPT).status_code, 409)
        self.assertEqual(self.client.delete(f"/api/creator/projects/{self.pid}").status_code, 409)

    def test_voice_preview_generates_wav_without_changing_project(self):
        selected={"name":"test","voice":"test-voice","ready":True}
        def fake_synthesize(text,path,engine):
            self.assertIn("preview",text.lower())
            self.assertEqual(engine["voice"],"test-voice")
            return make_wav(path,0.25)
        before=self.client.get(f"/api/creator/projects/{self.pid}/narration").get_json()
        with patch.object(local_narration,"select_backend",return_value=selected), \
             patch.object(local_narration,"synthesize",side_effect=fake_synthesize) as synth:
            response=self.client.post("/api/creator/narration/preview",
                                      json={"engine":"test","voice":"test-voice"})
        self.assertEqual(response.status_code, 200, response.status)
        self.assertEqual(response.mimetype,"audio/wav")
        self.assertTrue(response.data.startswith(b"RIFF"))
        synth.assert_called_once()
        self.assertEqual(self.client.get(
            f"/api/creator/projects/{self.pid}/narration").get_json(),before)

    def test_voice_preview_rejects_bad_selection_and_concurrency(self):
        endpoint="/api/creator/narration/preview"
        for payload in ({},{"engine":123,"voice":"test"},
                        {"engine":"test","voice":"test","text":"arbitrary"},
                        {"engine":"test","voice":None}):
            self.assertEqual(self.client.post(endpoint,json=payload).status_code,400)
        with patch.object(local_narration,"select_backend",
                          side_effect=local_narration.NarrationError("Voice not available")):
            self.assertEqual(self.client.post(endpoint,json={
                "engine":"invalid","voice":"missing"}).status_code,400)
        with patch.object(local_narration,"select_backend",
                          return_value={"name":"test","voice":"test","ready":True}):
            narration._preview_lock.acquire()
            try:
                self.assertEqual(self.client.post(endpoint,json={
                    "engine":"test","voice":"test"}).status_code,409)
            finally:
                narration._preview_lock.release()

    def test_voice_changes_blocked_during_neural_video_generation(self):
        self.client.put(f"/api/creator/projects/{self.pid}/script", json=SCRIPT)
        db.execute("UPDATE creator_projects SET status='generating_video' WHERE id=?",
                   (self.pid,))
        with patch.object(local_narration, "select_backend", return_value={
                 "name": "test", "voice": "test", "ready": True}):
            response = self.client.post(
                f"/api/creator/projects/{self.pid}/narration/generate")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.client.get(
            f"/api/creator/projects/{self.pid}/narration").get_json()
            ["narration"]["status"], "pending")

    def test_narration_queue_shutdown_preserves_existing_stages(self):
        import json
        self.client.put(f"/api/creator/projects/{self.pid}/script", json=SCRIPT)
        saved = json.dumps({"run_id": "a" * 32, "segments": []})
        db.execute("UPDATE creator_projects SET status='narration_ready' WHERE id=?",
                   (self.pid,))
        for stage in ("narration", "captions", "render"):
            db.execute("UPDATE creator_stages SET status='done',artifact_json=? "
                       "WHERE project_id=? AND stage=?", (saved, self.pid, stage))
        with patch.object(local_narration, "select_backend", return_value={
                 "name": "test", "voice": "test", "ready": True}), \
             patch.object(narration._executor, "submit",
                          side_effect=RuntimeError("executor is shutting down")):
            response = self.client.post(
                f"/api/creator/projects/{self.pid}/narration/generate")
        self.assertEqual(response.status_code, 503)
        project = self.client.get(
            f"/api/creator/projects/{self.pid}").get_json()["project"]
        self.assertEqual(project["status"], "narration_ready")
        for stage in ("narration", "captions", "render"):
            state = db.query_one(
                "SELECT status,artifact_json FROM creator_stages "
                "WHERE project_id=? AND stage=?", (self.pid, stage))
            self.assertEqual(state["status"], "done")
            self.assertEqual(state["artifact_json"], saved)

    def test_narration_queue_shutdown_preserves_pending_stages(self):
        self.client.put(f"/api/creator/projects/{self.pid}/script", json=SCRIPT)
        with patch.object(local_narration, "select_backend", return_value={
                 "name": "test", "voice": "test", "ready": True}), \
             patch.object(narration._executor, "submit",
                          side_effect=RuntimeError("executor shut down")):
            response = self.client.post(
                f"/api/creator/projects/{self.pid}/narration/generate")
        self.assertEqual(response.status_code, 503)
        project = self.client.get(
            f"/api/creator/projects/{self.pid}").get_json()["project"]
        self.assertEqual(project["status"], "script_ready")
        for stage in ("narration", "captions", "render"):
            self.assertEqual(project["stages"][stage]["status"], "pending")

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
