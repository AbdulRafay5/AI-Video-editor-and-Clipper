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
        self.patch_media = patch("config.DATA_DIR", Path(self.tmp.name))
        self.patch_media.start()
        db.init_db()
        app = Flask(__name__)
        app.register_blueprint(creator.bp)
        self.client = app.test_client()

    def tearDown(self):
        self.patch_media.stop()
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

    def test_setup_diagnostics_check_prerequisites_without_model_execution(self):
        from clipper import local_imagegen, local_musicgen, local_text_to_video
        with patch.object(creator.shutil, "which", side_effect=lambda x: "/bin/" + x if x=="ffmpeg" else None), \
             patch.object(creator.llm, "status", return_value={
                 "running": True, "installed": True, "model": "qwen3:4b",
                 "message": "Local model ready"}), \
             patch.object(local_imagegen, "dependencies_ready", return_value=True), \
             patch.object(local_musicgen, "dependencies_ready", return_value=False), \
             patch.object(local_text_to_video, "dependencies_ready", return_value=False):
            response = self.client.get("/api/creator/diagnostics")
        self.assertEqual(response.status_code, 200)
        data=response.get_json()
        self.assertFalse(data["ready_for_render"])
        self.assertTrue(data["ready_for_ai_script"])
        self.assertTrue(data["optional"]["ai_images"])
        self.assertFalse(data["optional"]["musicgen"])
        self.assertFalse(data["optional"]["neural_video"])
        self.assertIn("not validated", data["note"])

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

    def test_script_changes_blocked_during_neural_video_generation(self):
        pid = self.create()
        db.execute("UPDATE creator_projects SET status='generating_video' WHERE id=?",
                   (pid,))
        self.assertEqual(self.client.put(
            f"/api/creator/projects/{pid}/script", json=EXAMPLE).status_code, 409)
        self.assertEqual(self.client.post(
            f"/api/creator/projects/{pid}/script/generate").status_code, 409)

    def test_script_queue_shutdown_preserves_saved_script(self):
        pid = self.create()
        self.client.put(f"/api/creator/projects/{pid}/script", json=EXAMPLE)
        with patch.object(creator.llm, "status", return_value={
                 "running": True, "installed": True, "message": "ready"}), \
             patch.object(creator._executor, "submit",
                          side_effect=RuntimeError("cannot schedule new futures")):
            response = self.client.post(f"/api/creator/projects/{pid}/script/generate")
        self.assertEqual(response.status_code, 503)
        project = self.client.get(f"/api/creator/projects/{pid}").get_json()["project"]
        self.assertEqual(project["status"], "script_ready")
        self.assertEqual(project["script"]["title"], EXAMPLE["title"])
        self.assertEqual(project["stages"]["script"]["status"], "done")

    def test_script_queue_shutdown_preserves_finished_video_status(self):
        pid = self.create()
        self.client.put(f"/api/creator/projects/{pid}/script", json=EXAMPLE)
        db.execute("UPDATE creator_projects SET status='render_ready' WHERE id=?", (pid,))
        with patch.object(creator.llm, "status", return_value={
                 "running": True, "installed": True, "message": "ready"}), \
             patch.object(creator._executor, "submit",
                          side_effect=RuntimeError("executor shut down")):
            response = self.client.post(f"/api/creator/projects/{pid}/script/generate")
        self.assertEqual(response.status_code, 503)
        project = self.client.get(f"/api/creator/projects/{pid}").get_json()["project"]
        self.assertEqual(project["status"], "render_ready")
        self.assertEqual(project["stages"]["script"]["status"], "done")

    def test_script_queue_shutdown_returns_new_project_to_draft(self):
        pid = self.create()
        with patch.object(creator.llm, "status", return_value={
                 "running": True, "installed": True, "message": "ready"}), \
             patch.object(creator._executor, "submit",
                          side_effect=RuntimeError("executor shut down")):
            response = self.client.post(f"/api/creator/projects/{pid}/script/generate")
        self.assertEqual(response.status_code, 503)
        project = self.client.get(f"/api/creator/projects/{pid}").get_json()["project"]
        self.assertEqual(project["status"], "draft")
        self.assertEqual(project["stages"]["script"]["status"], "failed")

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

    def test_deleting_project_removes_only_its_media(self):
        import config
        pid = self.create()
        parent = config.DATA_DIR / "creator"
        root = parent / str(pid)
        other = parent / "unrelated"
        root.mkdir(parents=True)
        other.mkdir(parents=True)
        (root / "clip.mp4").write_bytes(b"source")
        (other / "retain.mp4").write_bytes(b"unrelated")
        response = self.client.delete(f"/api/creator/projects/{pid}")
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertFalse(root.exists())
        self.assertTrue((other / "retain.mp4").is_file())
        self.assertIsNone(db.query_one(
            "SELECT id FROM creator_projects WHERE id=?", (pid,)))

    def test_cannot_delete_a_project_in_active_automation(self):
        pid = self.create()
        db.execute("INSERT INTO creator_auto_jobs(project_id,status,step) "
                   "VALUES (?,'running','footage')", (pid,))
        response = self.client.delete(f"/api/creator/projects/{pid}")
        self.assertEqual(response.status_code, 409, response.get_json())
        self.assertIsNotNone(db.query_one(
            "SELECT id FROM creator_projects WHERE id=?", (pid,)))

    def test_deletion_reports_local_cleanup_failure(self):
        import config
        pid = self.create()
        root = config.DATA_DIR / "creator" / str(pid)
        root.mkdir(parents=True)
        with patch.object(creator.shutil, "rmtree", side_effect=OSError("access denied")):
            response = self.client.delete(f"/api/creator/projects/{pid}")
        self.assertEqual(response.status_code, 200)
        self.assertIn("cleanup failed", response.get_json()["warning"])
        self.assertIsNone(db.query_one(
            "SELECT id FROM creator_projects WHERE id=?", (pid,)))

    def test_deleting_project_removes_stage_rows(self):
        pid = self.create()
        self.assertEqual(self.client.delete(f"/api/creator/projects/{pid}").status_code, 200)
        self.assertIsNone(db.query_one("SELECT id FROM creator_projects WHERE id=?", (pid,)))
        self.assertEqual(db.query("SELECT * FROM creator_stages WHERE project_id=?", (pid,)), [])


if __name__ == "__main__":
    unittest.main()
