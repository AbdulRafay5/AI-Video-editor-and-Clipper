"""Offline tests for rejecting and replacing unsuitable stock B-roll."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from clipper import creator_api, creator_render_api as render, creator_stock_api as stock, db


SCRIPT = {"title": "B-roll review", "hook": "Look here",
          "scenes": [{"narration": "A short street scene", "visual_idea": "City street"}],
          "closing": "The end"}


class StockReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dbpatch = patch.object(db.config, "DB_PATH", Path(self.temp.name) / "db.sqlite")
        self.datapatch = patch.object(render.config, "DATA_DIR", Path(self.temp.name))
        self.dbpatch.start()
        self.datapatch.start()
        db.init_db()
        app = Flask(__name__)
        app.register_blueprint(creator_api.bp)
        app.register_blueprint(render.bp)
        app.register_blueprint(stock.bp)
        self.client = app.test_client()
        created = self.client.post("/api/creator/projects",
                                   json={"topic": "Create city B-roll scenes", "target_seconds": 30})
        self.pid = created.get_json()["project"]["id"]
        self.client.put(f"/api/creator/projects/{self.pid}/script", json=SCRIPT)
        self.reject = f"/api/creator/projects/{self.pid}/stock/reject/scene_0"
        self.clip = "clip_" + "a" * 32 + ".mp4"

    def tearDown(self):
        self.datapatch.stop()
        self.dbpatch.stop()
        self.temp.cleanup()

    def saved(self, stock_source=True):
        folder = render._root(self.pid) / "visuals"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / self.clip).write_bytes(b"original immutable footage")
        source = {"provider": "pixabay", "asset_id": 123,
                  "download_url": "https://cdn.pixabay.com/video/123.mp4",
                  "source_page": "https://pixabay.com/videos/demo"} if stock_source else None
        artifact = {"clips": {"scene_0": self.clip},
                    "sources": {"scene_0": source} if source else {}}
        db.execute("UPDATE creator_stages SET status='done', artifact_json=? "
                   "WHERE project_id=? AND stage='visuals'",
                   (json.dumps(artifact), self.pid))
        db.execute("UPDATE creator_stages SET status='done' "
                   "WHERE project_id=? AND stage='render'", (self.pid,))
        return folder / self.clip

    def test_reject_preserves_file_and_invalidates_render(self):
        footage = self.saved()
        response = self.client.post(self.reject)
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertTrue(footage.exists())
        state = self.client.get(f"/api/creator/projects/{self.pid}/visuals").get_json()
        self.assertNotIn("scene_0", state["clips"])
        row = db.query_one("SELECT status,artifact_json FROM creator_stages "
                           "WHERE project_id=? AND stage='visuals'", (self.pid,))
        artifact = json.loads(row["artifact_json"])
        self.assertEqual(artifact["rejected_assets"], [{"provider": "pixabay", "asset_id": "123"}])
        self.assertEqual(row["status"], "pending")
        render_state = db.query_one("SELECT status FROM creator_stages "
                                    "WHERE project_id=? AND stage='render'", (self.pid,))
        self.assertEqual(render_state["status"], "pending")
        self.assertEqual(self.client.post(self.reject).status_code, 409)

    def test_manual_footage_cannot_be_rejected_as_stock(self):
        self.saved(stock_source=False)
        self.assertEqual(self.client.post(self.reject).status_code, 409)
        self.assertIn("scene_0", self.client.get(
            f"/api/creator/projects/{self.pid}/visuals").get_json()["clips"])

    def test_busy_job_rejects_review_changes(self):
        self.saved()
        db.execute("UPDATE creator_projects SET status='rendering' WHERE id=?", (self.pid,))
        self.assertEqual(self.client.post(self.reject).status_code, 409)

    def test_invalid_slot_rejected(self):
        self.saved()
        self.assertEqual(self.client.post(
            f"/api/creator/projects/{self.pid}/stock/reject/scene_99").status_code, 404)

    def test_rejected_history_survives_stock_resave(self):
        self.saved()
        self.client.post(self.reject)
        original = db.query_one("SELECT artifact_json FROM creator_stages "
                                "WHERE project_id=? AND stage='visuals'", (self.pid,))
        artifact = json.loads(original["artifact_json"])
        # The stock worker merges old metadata instead of deleting rejection history.
        merged = {**artifact, "clips": {"scene_0": "clip_new.mp4"},
                  "sources": {"scene_0": {"provider": "pexels", "asset_id": "456"}}}
        self.assertIn({"provider": "pixabay", "asset_id": "123"}, merged["rejected_assets"])


if __name__ == "__main__":
    unittest.main()
