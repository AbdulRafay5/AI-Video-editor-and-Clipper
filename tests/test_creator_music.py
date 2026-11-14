"""No-cost instrumental generation, preset library, random selection and isolation."""
import json
import shutil
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from flask import Flask
from clipper import creator_api, creator_render_api as render, creator_music_api as music, db


@unittest.skipUnless(shutil.which("ffprobe"), "FFprobe unavailable")
class MusicTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dbp = patch.object(db.config, "DB_PATH", Path(self.temp.name) / "db.sqlite")
        self.datap = patch.object(music.config, "DATA_DIR", Path(self.temp.name))
        self.dbp.start()
        self.datap.start()
        db.init_db()
        app = Flask(__name__)
        app.register_blueprint(creator_api.bp)
        app.register_blueprint(render.bp)
        app.register_blueprint(music.bp)
        self.client = app.test_client()
        result = self.client.post("/api/creator/projects",
                                  json={"topic":"Make a video about local music tracks", "target_seconds":30})
        self.pid = result.get_json()["project"]["id"]

    def tearDown(self):
        self.datap.stop()
        self.dbp.stop()
        self.temp.cleanup()

    def test_generates_original_wav_and_reuses_as_preset(self):
        generated = self.client.post(
            f"/api/creator/projects/{self.pid}/music/generate",
            json={"mood": "ambient", "seconds": 5})
        self.assertEqual(generated.status_code, 201, generated.get_data(as_text=True))
        stage = render._music(self.pid)
        self.assertIsNotNone(stage)
        with wave.open(str(stage["file"])) as audio:
            self.assertEqual(audio.getnchannels(), 1)
            self.assertAlmostEqual(audio.getnframes()/audio.getframerate(), 5, delta=0.01)
        saved = self.client.post(f"/api/creator/projects/{self.pid}/music/save-preset")
        self.assertEqual(saved.status_code, 201)
        name = saved.get_json()["filename"]
        self.assertIn(name, self.client.get("/api/creator/music/presets").get_json()["saved"])
        selected = self.client.post(
            f"/api/creator/projects/{self.pid}/music/select-preset",
            json={"filename": "random"})
        self.assertEqual(selected.status_code, 201, selected.get_data(as_text=True))
        self.assertEqual(render._music(self.pid)["duration"], 5.0)

    def test_rejects_invalid_generated_mood(self):
        result = self.client.post(f"/api/creator/projects/{self.pid}/music/generate",
                                  json={"mood": "paid-ai", "seconds": 15})
        self.assertEqual(result.status_code, 400)

    def test_rejects_arbitrary_preset_filenames(self):
        result = self.client.post(
            f"/api/creator/projects/{self.pid}/music/select-preset",
            json={"filename": "../../private.mp3"})
        self.assertEqual(result.status_code, 400)


if __name__ == "__main__":
    unittest.main()
