"""Automatic free stock footage: controlled downloads and legacy isolation."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from flask import Flask
from clipper import creator_api, creator_render_api as render, creator_stock_api as stock, db

SCRIPT = {
    "title": "Coffee documentary",
    "hook": "A coffee shop grew from nothing.",
    "scenes": [{"narration": "The barista serves many returning customers.",
                "visual_idea": "barista pouring fresh coffee"}],
    "closing": "Every returning customer matters."
}


class CreatorStockTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = patch.object(db.config, "DB_PATH", Path(self.temp.name) / "test.sqlite")
        self.data_path = patch.object(render.config, "DATA_DIR", Path(self.temp.name))
        self.db_path.start()
        self.data_path.start()
        db.init_db()
        app = Flask(__name__)
        app.register_blueprint(creator_api.bp)
        app.register_blueprint(render.bp)
        app.register_blueprint(stock.bp)
        self.client = app.test_client()
        response = self.client.post("/api/creator/projects",
                                    json={"topic": "How small coffee shops work", "target_seconds": 30})
        self.pid = response.get_json()["project"]["id"]
        self.client.put(f"/api/creator/projects/{self.pid}/script", json=SCRIPT)

    def tearDown(self):
        self.data_path.stop()
        self.db_path.stop()
        self.temp.cleanup()

    def test_unapproved_download_never_starts(self):
        with patch.dict("os.environ", {"PIXABAY_API_KEY": "fake"}):
            with patch.object(stock._executor, "submit") as submit:
                response = self.client.post(
                    f"/api/creator/projects/{self.pid}/stock/auto-fill",
                    json={"approve_download": False})
        self.assertEqual(response.status_code, 400)
        submit.assert_not_called()

    def test_missing_key_stays_free_and_no_task(self):
        with patch.dict("os.environ", {"PIXABAY_API_KEY": ""}):
            with patch.object(stock._executor, "submit") as submit:
                response = self.client.post(
                    f"/api/creator/projects/{self.pid}/stock/auto-fill",
                    json={"approve_download": True})
        self.assertEqual(response.status_code, 503)
        submit.assert_not_called()

    def test_cannot_source_over_active_work(self):
        db.execute("UPDATE creator_projects SET status='rendering' WHERE id=?", (self.pid,))
        with patch.dict("os.environ", {"PIXABAY_API_KEY": "fake"}):
            result = self.client.post(f"/api/creator/projects/{self.pid}/stock/auto-fill",
                                      json={"approve_download": True})
        self.assertEqual(result.status_code, 409)

    def test_candidate_rules_filter_invalid_links_and_prioritise_portrait(self):
        data = {"hits": [
            {"id": 1, "duration": 7, "pageURL": "https://pixabay.com/videos/example",
             "videos": {
                 "large": {"url": "https://cdn.pixabay.com/video/land.mp4",
                           "width": 1920, "height": 1080},
                 "medium": {"url": "https://cdn.pixabay.com/video/portrait.mp4",
                            "width": 720, "height": 1280},
                 "small": {"url": "https://evil.example/video.mp4",
                           "width": 640, "height": 1100}}}]}
        results = stock._candidates(data)
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0]["portrait"])
        self.assertTrue(results[0]["download_url"].endswith("/portrait.mp4"))

    def test_completed_download_preserves_manual_media(self):
        manual = render._root(self.pid) / "visuals" / "clip_manual.mp4"
        manual.parent.mkdir(parents=True, exist_ok=True)
        manual.write_bytes(b"manual footage")
        db.execute("UPDATE creator_stages SET artifact_json=? WHERE project_id=? AND stage='visuals'",
                   (json.dumps({"clips": {"hook": manual.name}}), self.pid))
        candidate = {
            "id": 123, "duration": 8, "width": 720, "height": 1280,
            "download_url": "https://cdn.pixabay.com/videos/abc.mp4",
            "source_page": "https://pixabay.com/videos/abc",
            "creator": "Artist",
        }
        def fake_download(url, path, provider="pixabay"):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"local fake video")
        with patch.object(stock.providers, "configured", return_value=["pixabay"]), \
             patch.object(stock.providers, "search", return_value=[candidate]), \
             patch.object(stock, "_download", side_effect=fake_download):
            stock._run(self.pid, ["scene_0"])
        result = self.client.get(f"/api/creator/projects/{self.pid}/visuals").get_json()
        self.assertEqual(result["clips"]["hook"], manual.name)
        self.assertIn("scene_0", result["clips"])
        status = self.client.get(f"/api/creator/projects/{self.pid}/stock").get_json()
        self.assertEqual(status["status"], "done")
        self.assertIn("scene_0", status["report"]["applied"])

    def test_failed_search_keeps_existing_visuals(self):
        with patch.object(stock.providers, "configured", return_value=["pixabay"]), \
             patch.object(stock.providers, "search", return_value=[]):
            stock._run(self.pid, ["scene_0"])
        status = self.client.get(f"/api/creator/projects/{self.pid}/stock").get_json()
        self.assertIn("scene_0", status["report"]["errors"])
        self.assertEqual(self.client.get(
            f"/api/creator/projects/{self.pid}/visuals").get_json()["clips"], {})

    def test_script_changes_protected_during_sourcing(self):
        db.execute("UPDATE creator_projects SET status='sourcing_visuals' WHERE id=?", (self.pid,))
        self.assertEqual(self.client.put(f"/api/creator/projects/{self.pid}/script",
                                         json=SCRIPT).status_code, 409)
        self.assertEqual(self.client.delete(
            f"/api/creator/projects/{self.pid}").status_code, 409)

    def test_restart_recovers_interrupted_stock(self):
        db.execute("UPDATE creator_projects SET status='sourcing_visuals' WHERE id=?", (self.pid,))
        db.execute("INSERT OR REPLACE INTO creator_stock_jobs(project_id,status) VALUES (?,?)",
                   (self.pid, "running"))
        db.init_db()
        self.assertEqual(render._project(self.pid)["status"], "script_ready")
        status = self.client.get(f"/api/creator/projects/{self.pid}/stock").get_json()
        self.assertEqual(status["status"], "failed")


if __name__ == "__main__":
    unittest.main()
