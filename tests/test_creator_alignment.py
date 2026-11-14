"""Local word-level alignment, no model download or network."""
import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch
from flask import Flask

from clipper import creator_api, creator_alignment_api as alignment, creator_finish_api as finish, db


SCRIPT = {"title": "Demo", "hook": "Starting today.",
          "scenes": [{"narration": "A new project with spoken narration.",
                      "visual_idea": "People at desks"}], "closing": "Thanks for watching."}


class AlignmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(db.config, "DB_PATH", Path(self.temp.name) / "db.sqlite")
        self.data_patch = patch.object(alignment.creator.config, "DATA_DIR", Path(self.temp.name))
        self.db_patch.start()
        self.data_patch.start()
        db.init_db()
        app = Flask(__name__)
        app.register_blueprint(creator_api.bp)
        app.register_blueprint(alignment.bp)
        app.register_blueprint(finish.bp)
        self.client = app.test_client()
        result = self.client.post("/api/creator/projects",
                                  json={"topic": "Writing a short technology video", "target_seconds": 30})
        self.pid = result.get_json()["project"]["id"]
        self.client.put(f"/api/creator/projects/{self.pid}/script", json=SCRIPT)
        self.run_id = "d"*32
        path = Path(self.temp.name) / "creator" / str(self.pid) / "narration" / self.run_id
        path.mkdir(parents=True, exist_ok=True)
        (path / "full.wav").write_bytes(b"mocked speech")
        self.artifact = {
            "run_id": self.run_id, "duration": 3,
            "segments": [{"start":0,"end":1,"text":SCRIPT["hook"]},
                         {"start":1,"end":2,"text":SCRIPT["scenes"][0]["narration"]},
                         {"start":2,"end":3,"text":SCRIPT["closing"]}],
        }
        db.execute("UPDATE creator_stages SET status='done', artifact_json=? "
                   "WHERE project_id=? AND stage='narration'",
                   (json.dumps(self.artifact), self.pid))

    def tearDown(self):
        self.data_patch.stop()
        self.db_patch.stop()
        self.temp.cleanup()

    def test_uses_model_word_timestamps_when_available(self):
        cue = types.SimpleNamespace(word=" Starting", start=0.3, end=0.8)
        fake_model = types.SimpleNamespace(transcribe=lambda *a, **kw:
                                           (iter([types.SimpleNamespace(words=[cue])]), None))
        fake_module = types.SimpleNamespace(WhisperModel=lambda *a, **kw: fake_model)
        with patch.dict("sys.modules", {"faster_whisper": fake_module}):
            alignment._align(self.pid,
                Path(self.temp.name) / "creator" / str(self.pid) / "narration" / self.run_id / "full.wav",
                self.run_id, 3.0)
        stage = db.query_one("SELECT artifact_json,status FROM creator_stages "
                             "WHERE project_id=? AND stage='captions'", (self.pid,))
        self.assertEqual(stage["status"], "done")
        saved = json.loads(stage["artifact_json"])
        self.assertEqual(saved["aligned_words"][0]["start"], 0.3)
        captions, source = finish.project_captions(self.pid)
        self.assertEqual(source, "whisper")
        self.assertAlmostEqual(captions[0]["start"], 0.3)
        self.assertAlmostEqual(captions[0]["end"], 0.8)

    def test_old_alignment_invalidated_when_narration_changes(self):
        db.execute("UPDATE creator_stages SET status='done', artifact_json=? "
                   "WHERE project_id=? AND stage='captions'",
                   (json.dumps({"aligned_words": [{"word": "Wrong", "start": 0.1, "end": 0.6}],
                                "narration_run_id": "f"*32}), self.pid))
        _, source = finish.project_captions(self.pid)
        self.assertEqual(source, "estimated")

    def test_cannot_start_alignment_during_render(self):
        db.execute("UPDATE creator_projects SET status='rendering' WHERE id=?", (self.pid,))
        r = self.client.post(f"/api/creator/projects/{self.pid}/captions/align")
        self.assertEqual(r.status_code, 409)

    def test_restart_recovers_alignment(self):
        db.execute("UPDATE creator_projects SET status='aligning_captions' WHERE id=?", (self.pid,))
        db.execute("UPDATE creator_stages SET status='running' WHERE project_id=? "
                   "AND stage='captions'", (self.pid,))
        db.init_db()
        self.assertEqual(db.query_one("SELECT status FROM creator_projects WHERE id=?",
                                       (self.pid,))["status"], "narration_ready")


if __name__ == "__main__":
    unittest.main()
