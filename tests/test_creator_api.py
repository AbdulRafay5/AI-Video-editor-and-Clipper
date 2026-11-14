"""Creator Studio API regression tests; local SQLite, fake Ollama, no network."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from clipper import creator_api as creator, db


EXAMPLE = {
    "title": "How a coffee shop started",
    "hook": "The café had zero customers on day one.",
    "scenes": [
        {"narration": "The founders began selling coffee in a tiny shop.",
         "visual_idea": "Real barista at a counter"},
        {"narration": "Their regular customers recommended them to friends.",
         "visual_idea": "Coffee being prepared"},
    ],
    "closing": "Good businesses earn trust one customer at a time.",
}


class CreatorWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patch_db = patch.object(db.config, "DB_PATH", Path(self.tmp.name) / "test.sqlite")
        self.patch_db.start()
        db.init_db()
        app = Flask(__name__)
        app.register_blueprint(creator.bp)
        self.client = app.test_client()

    def tearDown(self):
        self.patch_db.stop()
        self.tmp.cleanup()

    def create(self):
        response = self.client.post("/api/creator/projects", json={
            "topic": "A little coffee shop grows through customer referrals",
            "audience": "new entrepreneurs",
            "target_seconds": 60,
        })
        self.assertEqual(response.status_code, 201)
        return response.get_json()["project"]["id"]

    def test_project_persists_stages(self):
        pid = self.create()
        project = self.client.get(f"/api/creator/projects/{pid}").get_json()["project"]
        self.assertEqual(project["status"], "draft")
        self.assertEqual(set(project["stages"]), set(creator.STAGES))
        self.assertTrue(all(row["status"] == "pending" for row in project["stages"].values()))

    def test_invalid_topic_and_length_are_rejected(self):
        self.assertEqual(self.client.post("/api/creator/projects",
            json={"topic": "a", "target_seconds": 60}).status_code, 400)
        self.assertEqual(self.client.post("/api/creator/projects",
            json={"topic": "Valid interesting topic", "target_seconds": 10000}).status_code, 400)

    def test_manual_script_save_and_stage_invalidation(self):
        pid = self.create()
        saved = self.client.put(f"/api/creator/projects/{pid}/script", json=EXAMPLE)
        self.assertEqual(saved.status_code, 200)
        project = saved.get_json()["project"]
        self.assertEqual(project["script"]["scenes"][0]["narration"], EXAMPLE["scenes"][0]["narration"])
        self.assertEqual(project["stages"]["script"]["status"], "done")
        self.assertEqual(project["stages"]["narration"]["status"], "pending")

    def test_invalid_script_is_not_saved(self):
        pid = self.create()
        invalid = dict(EXAMPLE, scenes=[])
        self.assertEqual(
            self.client.put(f"/api/creator/projects/{pid}/script", json=invalid).status_code,
            400)
        self.assertIsNone(self.client.get(f"/api/creator/projects/{pid}").get_json()["project"]["script"])

    def test_generation_blocks_when_ollama_missing(self):
        pid = self.create()
        with patch.object(creator.llm, "status", return_value={
            "running": False, "installed": False, "message": "Ollama offline"
        }):
            with patch.object(creator._executor, "submit") as queue:
                response = self.client.post(f"/api/creator/projects/{pid}/script/generate")
        self.assertEqual(response.status_code, 503)
        queue.assert_not_called()

    def test_worker_saves_valid_generated_script(self):
        pid = self.create()
        with patch.object(creator.llm, "chat_json", return_value=(EXAMPLE, {})) as model:
            creator._run_script(pid)
        project = self.client.get(f"/api/creator/projects/{pid}").get_json()["project"]
        self.assertEqual(project["status"], "script_ready")
        self.assertEqual(project["stages"]["script"]["status"], "done")
        model.assert_called_once()

    def test_script_job_recovered_after_restart(self):
        pid = self.create()
        db.execute("UPDATE creator_projects SET status='generating_script' WHERE id=?", (pid,))
        db.execute("UPDATE creator_stages SET status='running' WHERE project_id=? AND stage='script'", (pid,))
        db.init_db()
        project = self.client.get(f"/api/creator/projects/{pid}").get_json()["project"]
        self.assertEqual(project["status"], "failed")
        self.assertEqual(project["stages"]["script"]["status"], "failed")

    def test_deleting_project_removes_stage_rows(self):
        pid = self.create()
        self.assertEqual(self.client.delete(f"/api/creator/projects/{pid}").status_code, 200)
        self.assertIsNone(db.query_one("SELECT id FROM creator_projects WHERE id=?", (pid,)))
        self.assertEqual(db.query("SELECT * FROM creator_stages WHERE project_id=?", (pid,)), [])


if __name__ == "__main__":
    unittest.main()
