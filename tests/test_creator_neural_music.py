"""Neural soundtrack workflow and model integration tests without network or model downloads."""
import contextlib
import json
import math
import shutil
import tempfile
import types
import unittest
import wave
from pathlib import Path
from unittest.mock import patch
from flask import Flask

from clipper import creator_api, creator_render_api as creator
from clipper import creator_neural_music_api as neural, local_musicgen as musicgen, db


class PromptTests(unittest.TestCase):
    def test_video_topic_and_scenes_inform_soundtrack(self):
        text = musicgen.build_prompt("Rainy city photography", {
            "scenes": [{"visual_idea": "Night streets and rain"},
                       {"visual_idea": "Warm light from a cafe"}]}, "calm")
        self.assertIn("Rainy city photography", text)
        self.assertIn("Night streets", text)
        self.assertIn("gentle piano", text)
        self.assertIn("no vocals", text)
        self.assertLessEqual(len(text), 500)

    def test_custom_prompt_is_not_discarded(self):
        value = "dreamy synth pop without vocals"
        self.assertEqual(musicgen.build_prompt("Topic", {}, custom=value), value)

    def test_music_length_matches_narration_not_topic_estimate(self):
        self.assertEqual(musicgen.soundtrack_seconds(
            {"target_seconds": 30}, {"duration": 42.1}), 43)
        self.assertEqual(musicgen.soundtrack_seconds(
            {"target_seconds": 60}, None), 60)
        self.assertEqual(musicgen.soundtrack_seconds(
            {"target_seconds": 500}, None), 180)

    def test_rejects_bad_and_oversized_prompts(self):
        with self.assertRaises(musicgen.MusicGenError):
            musicgen.build_prompt("Topic", {}, custom="x" * 501)
        with self.assertRaises(musicgen.MusicGenError):
            musicgen.build_prompt("Topic", {}, mood="unknown")

    def test_waveform_crossfade_is_bounded(self):
        import numpy as np
        source = np.sin(np.linspace(0, 8 * math.pi, 16000, dtype=np.float32))
        track = musicgen._seamless_repeat(source, 16000 * 6, 16000, np)
        self.assertEqual(len(track), 16000 * 6)
        self.assertTrue(np.all(np.isfinite(track)))
        self.assertLessEqual(float(np.max(np.abs(track))), 0.95)
        self.assertAlmostEqual(float(track[-1]), 0, delta=0.005)

    def test_optional_dependency_check_does_not_download(self):
        with patch.object(musicgen, "dependencies_ready", return_value=False):
            with self.assertRaisesRegex(musicgen.MusicGenError, "optional local MusicGen"):
                musicgen.generate("/tmp/unused.wav", "ambient piano", 10)


class NeuralApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dbpatch = patch.object(db.config, "DB_PATH", Path(self.temp.name) / "database.sqlite")
        self.pathpatch = patch.object(creator.config, "DATA_DIR", Path(self.temp.name))
        self.dbpatch.start()
        self.pathpatch.start()
        db.init_db()
        app = Flask(__name__)
        app.register_blueprint(creator_api.bp)
        app.register_blueprint(creator.bp)
        app.register_blueprint(neural.bp)
        self.client = app.test_client()
        created = self.client.post("/api/creator/projects",
                                   json={"topic": "An atmospheric rain documentary",
                                         "target_seconds": 30})
        self.assertEqual(created.status_code, 201, created.get_data(as_text=True))
        self.pid = created.get_json()["project"]["id"]
        self.prefix = f"/api/creator/projects/{self.pid}/music/neural"

    def tearDown(self):
        self.pathpatch.stop()
        self.dbpatch.stop()
        self.temp.cleanup()

    def _fake_neural(self, path, prompt, seconds, **kw):
        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(16000)
            output.writeframes(b"\0\0" * 16000 * seconds)
        return {"duration": seconds, "sample_seconds": 8, "device": "cpu",
                "looped": seconds > 8, "model": musicgen.MODEL_ID}

    def test_capabilities_and_prompt_preview_work_without_deps(self):
        with patch.object(musicgen, "dependencies_ready", return_value=False):
            capabilities = self.client.get("/api/creator/music/neural/capabilities").get_json()
            self.assertFalse(capabilities["installed"])
        result = self.client.post(self.prefix + "/preview",
                                  json={"mood": "suspenseful", "duration": "match"})
        self.assertEqual(result.status_code, 200, result.get_data(as_text=True))
        self.assertEqual(result.get_json()["duration"], 30)
        self.assertIn("atmospheric rain documentary", result.get_json()["prompt"])

    def test_rejects_bad_prompt_and_missing_install(self):
        self.assertEqual(self.client.post(
            self.prefix + "/preview", json={"mood": "calm", "prompt": "x"*501}).status_code, 400)
        with patch.object(musicgen, "dependencies_ready", return_value=False):
            response = self.client.post(self.prefix + "/generate", json={"mood": "calm"})
        self.assertEqual(response.status_code, 503)
        self.assertNotEqual(creator._project(self.pid)["status"], "generating_music")

    def test_queued_job_then_local_success_invalidates_only_render(self):
        # Any old soundtrack is replaced only after generation and validation.
        with patch.object(musicgen, "dependencies_ready", return_value=True), \
             patch.object(neural._executor, "submit") as submit:
            response = self.client.post(self.prefix + "/generate",
                                        json={"mood": "calm", "duration": 6,
                                              "download_model": False})
        self.assertEqual(response.status_code, 202, response.get_data(as_text=True))
        submit.assert_called_once()
        self.assertEqual(creator._project(self.pid)["status"], "generating_music")
        self.assertEqual(self.client.get(self.prefix).get_json()["status"], "running")
        with patch.object(musicgen, "generate", side_effect=self._fake_neural):
            neural._generate(self.pid, submit.call_args.args[2])
        status = self.client.get(self.prefix).get_json()
        self.assertEqual(status["status"], "done")
        self.assertEqual(status["report"]["duration"], 6.0)
        self.assertEqual(creator._music(self.pid)["duration"], 6.0)
        self.assertEqual(self.client.get(self.prefix + "/audio").status_code, 200)
        self.assertNotEqual(creator._project(self.pid)["status"], "generating_music")

    def test_failure_preserves_previous_music(self):
        old_path = creator._root(self.pid) / "music" / "music_previous.wav"
        self._fake_neural(old_path, "previous", 6)
        db.execute("UPDATE creator_stages SET status='done',artifact_json=? "
                   "WHERE project_id=? AND stage='music'",
                   (json.dumps({"filename": old_path.name, "duration": 6,
                                "mode": "generated-calm"}), self.pid))
        db.execute("INSERT INTO creator_music_jobs(project_id,status) VALUES (?,'running')",
                   (self.pid,))
        db.execute("UPDATE creator_projects SET status='generating_music' WHERE id=?",
                   (self.pid,))
        with patch.object(musicgen, "generate",
                          side_effect=musicgen.MusicGenError("No local model cached.")):
            neural._generate(self.pid, {"prompt": "Soft piano", "seconds": 6,
                                        "mood": "calm", "allow_download": False})
        self.assertEqual(self.client.get(self.prefix).get_json()["status"], "failed")
        self.assertEqual(creator._music(self.pid)["filename"], old_path.name)
        self.assertTrue(old_path.is_file())
        self.assertNotEqual(creator._project(self.pid)["status"], "generating_music")

    def test_only_one_neural_generation_at_a_time(self):
        db.execute("UPDATE creator_projects SET status='generating_music' WHERE id=?",
                   (self.pid,))
        with patch.object(musicgen, "dependencies_ready", return_value=True):
            self.assertEqual(self.client.post(
                self.prefix + "/generate", json={"mood": "calm"}).status_code, 409)

    def test_restart_recovers_music_job_without_changing_audio(self):
        db.execute("UPDATE creator_projects SET status='generating_music' WHERE id=?",
                   (self.pid,))
        db.execute("INSERT INTO creator_music_jobs(project_id,status) VALUES (?,'running')",
                   (self.pid,))
        db.init_db()
        self.assertEqual(self.client.get(self.prefix).get_json()["status"], "failed")
        self.assertNotEqual(creator._project(self.pid)["status"], "generating_music")


if __name__ == "__main__":
    unittest.main()
