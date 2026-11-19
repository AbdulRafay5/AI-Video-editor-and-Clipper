"""Recorded narration import: offline API regression coverage."""
import io
import json
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch
from flask import Flask

from clipper import creator_api, creator_narration_api as narration, db


SCRIPT = {
    "title": "Recording test", "hook": "First words.",
    "scenes": [{"narration": "This is the main narrated scene.",
                "visual_idea": "A forest"}],
    "closing": "Thanks for listening.",
}


def wav_bytes(seconds=0.25):
    with io.BytesIO() as buffer:
        with wave.open(buffer, "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(22050)
            audio.writeframes(b"\\0\\0" * int(22050 * seconds))
        return buffer.getvalue()


class RecordedNarrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dbpatch = patch.object(db.config, "DB_PATH", Path(self.temp.name) / "db.sqlite")
        self.datapatch = patch.object(narration.config, "DATA_DIR", Path(self.temp.name))
        self.dbpatch.start()
        self.datapatch.start()
        db.init_db()
        app = Flask(__name__)
        app.register_blueprint(creator_api.bp)
        app.register_blueprint(narration.bp)
        self.client = app.test_client()
        created = self.client.post("/api/creator/projects",
                                   json={"topic": "A recorded original video", "target_seconds": 30})
        self.pid = created.get_json()["project"]["id"]
        self.client.put(f"/api/creator/projects/{self.pid}/script", json=SCRIPT)
        self.url = f"/api/creator/projects/{self.pid}/narration/upload"

    def tearDown(self):
        self.datapatch.stop()
        self.dbpatch.stop()
        self.temp.cleanup()

    def payload(self, corrupt=None):
        contents = wav_bytes()
        return {"part_" + str(i): (io.BytesIO(b"bad" if i == corrupt else contents),
                                    "recording.wav") for i in range(3)}

    def test_rejects_missing_recordings_without_changing_status(self):
        with patch.object(narration.shutil, "which", return_value="ffmpeg"):
            result = self.client.post(self.url, data={"part_0": (io.BytesIO(wav_bytes()), "a.wav")})
        self.assertEqual(result.status_code, 400)
        self.assertEqual(db.query_one("SELECT status FROM creator_projects WHERE id=?",
                                      (self.pid,))["status"], "script_ready")

    def test_success_creates_compatible_artifact_and_invalidates_downstream(self):
        with patch.object(narration.shutil, "which", return_value="ffmpeg"), \\
             patch.object(narration.subprocess, "run", side_effect=self.fake_ffmpeg), \\
             patch.object(narration.local_narration, "combine", side_effect=self.fake_combine):
            result = self.client.post(self.url, data=self.payload())
        self.assertEqual(result.status_code, 201, result.get_json())
        artifact = result.get_json()["narration"]
        self.assertEqual(artifact["engine"], "recorded")
        self.assertEqual(len(artifact["segments"]), 3)
        self.assertEqual([x["start"] for x in artifact["segments"]], [0, 0.25, 0.5])
        self.assertEqual(artifact["duration"], 0.75)
        for i in range(3):
            response = self.client.get(
                f"/api/creator/projects/{self.pid}/narration/audio?segment={i}")
            self.assertEqual(response.status_code, 200)
        self.assertEqual(db.query_one("SELECT status FROM creator_stages WHERE project_id=? "
                                      "AND stage='render'", (self.pid,))["status"], "pending")

    @staticmethod
    def fake_ffmpeg(command, **kwargs):
        import shutil
        shutil.copyfile(command[command.index("-i") + 1], command[-1])
        return type("Result", (), {"returncode": 0})()

    @staticmethod
    def fake_combine(paths, output):
        output.write_bytes(wav_bytes(0.75))
        return 0.75

    def test_failed_upload_preserves_existing_narration(self):
        original = {"run_id": "a" * 32, "segments": [{"text": "Previous"}]}
        db.execute("UPDATE creator_stages SET status='done', artifact_json=? "
                   "WHERE project_id=? AND stage='narration'",
                   (json.dumps(original), self.pid))
        db.execute("UPDATE creator_projects SET status='narration_ready' WHERE id=?",
                   (self.pid,))
        with patch.object(narration.shutil, "which", return_value="ffmpeg"), \\
             patch.object(narration.subprocess, "run", side_effect=self.fake_ffmpeg):
            response = self.client.post(self.url, data=self.payload(corrupt=1))
        self.assertEqual(response.status_code, 422)
        saved = db.query_one("SELECT artifact_json FROM creator_stages "
                             "WHERE project_id=? AND stage='narration'", (self.pid,))
        self.assertEqual(json.loads(saved["artifact_json"]), original)
        self.assertEqual(db.query_one("SELECT status FROM creator_projects WHERE id=?",
                                      (self.pid,))["status"], "narration_ready")


if __name__ == "__main__":
    unittest.main()
