"""Batch Creator Studio API regression tests: no AI inference or downloads."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from clipper import creator_api, creator_batch_api as batch, db


class CreatorBatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(db.config, "DB_PATH", Path(self.tmp.name) / "test.db")
        self.db_patch.start()
        db.init_db()
        app = Flask(__name__)
        app.register_blueprint(creator_api.bp)
        app.register_blueprint(batch.bp)
        self.client = app.test_client()
        self.app = app
        self.submit = patch.object(batch._executor, "submit")
        self.submit.start()
        self.payload = {
            "topics": ["How coffee is roasted", "Why whales migrate"],
            "target_seconds": 30,
            "options": {"visual_mode": "ai", "music": "none"},
        }

    def tearDown(self):
        self.submit.stop()
        self.db_patch.stop()
        self.tmp.cleanup()

    def test_create_batch_persists_projects_and_stages(self):
        response = self.client.post("/api/creator/batches", json=self.payload)
        self.assertEqual(response.status_code, 202, response.get_json())
        data = response.get_json()["batch"]
        self.assertEqual(data["status"], "running")
        submitted = batch._executor.submit
        self.assertIs(submitted.call_args.args[1], data["id"])
        self.assertIs(submitted.call_args.args[2], self.app)
        self.assertEqual(data["counts"]["queued"], 2)
        self.assertEqual(len(data["items"]), 2)
        for item in data["items"]:
            self.assertEqual(item["project_status"], "draft")
            self.assertEqual(db.query_one(
                "SELECT COUNT(*) AS n FROM creator_stages WHERE project_id=?",
                (item["project_id"],))["n"], len(creator_api.STAGES))
        self.assertEqual(self.client.get(
            f"/api/creator/batches/{data['id']}").get_json()["batch"]["id"], data["id"])

    def test_rejects_invalid_topics_and_options_without_creating_projects(self):
        for change in (
            {"topics": ["only one"]},
            {"topics": ["valid first", " "]},
            {"topics": ["ok length", "valid second"], "target_seconds": 10},
            {"options": {"visual_mode": "bad"}},
            {"options": {"visual_mode": "neural_video", "approve_stock_download": True}},
        ):
            body = dict(self.payload, **change)
            response = self.client.post("/api/creator/batches", json=body)
            self.assertEqual(response.status_code, 400, (body, response.get_json()))
        self.assertFalse(db.query("SELECT * FROM creator_batches"))
        self.assertFalse(db.query("SELECT * FROM creator_projects"))

    def test_retry_preserves_completed_videos(self):
        response = self.client.post("/api/creator/batches", json=self.payload)
        bid = response.get_json()["batch"]["id"]
        items = response.get_json()["batch"]["items"]
        db.execute("UPDATE creator_batches SET status='blocked' WHERE id=?", (bid,))
        db.execute("UPDATE creator_batch_items SET status='done' WHERE id=?", (items[0]["id"],))
        db.execute("UPDATE creator_batch_items SET status='blocked',error='GPU memory' WHERE id=?",
                   (items[1]["id"],))
        retry = self.client.post(f"/api/creator/batches/{bid}/retry", json={})
        self.assertEqual(retry.status_code, 202)
        states = retry.get_json()["batch"]["items"]
        self.assertEqual([i["status"] for i in states], ["done", "queued"])
        self.assertIsNone(states[1]["error"])

    def test_pause_preserves_queued_items_and_resume(self):
        response = self.client.post("/api/creator/batches", json=self.payload)
        bid = response.get_json()["batch"]["id"]
        items = response.get_json()["batch"]["items"]
        pause = self.client.post(f"/api/creator/batches/{bid}/pause")
        self.assertEqual(pause.status_code, 200, pause.get_json())
        self.assertEqual(pause.get_json()["batch"]["status"], "paused")
        self.assertEqual([x["status"] for x in pause.get_json()["batch"]["items"]],
                         ["queued", "queued"])
        self.assertEqual(self.client.post(
            f"/api/creator/batches/{bid}/pause").status_code, 409)
        resumed = self.client.post(f"/api/creator/batches/{bid}/retry", json={})
        self.assertEqual(resumed.status_code, 202)
        self.assertEqual(resumed.get_json()["batch"]["status"], "running")
        self.assertEqual([x["id"] for x in resumed.get_json()["batch"]["items"]],
                         [x["id"] for x in items])

    def test_pause_waits_for_running_video_before_resume(self):
        response = self.client.post("/api/creator/batches", json=self.payload)
        bid = response.get_json()["batch"]["id"]
        first = response.get_json()["batch"]["items"][0]["id"]
        db.execute("UPDATE creator_batch_items SET status='running' WHERE id=?", (first,))
        self.assertEqual(self.client.post(
            f"/api/creator/batches/{bid}/pause").status_code, 200)
        retry = self.client.post(f"/api/creator/batches/{bid}/retry", json={})
        self.assertEqual(retry.status_code, 409)
        self.assertEqual(self.client.get(f"/api/creator/batches/{bid}")
                         .get_json()["batch"]["status"], "paused")
        db.execute("UPDATE creator_batch_items SET status='done' WHERE id=?", (first,))
        self.assertEqual(self.client.post(
            f"/api/creator/batches/{bid}/retry", json={}).status_code, 202)

    def test_claim_cannot_start_next_item_after_pause(self):
        response = self.client.post("/api/creator/batches", json=self.payload)
        bid = response.get_json()["batch"]["id"]
        first, second = response.get_json()["batch"]["items"]
        self.assertTrue(batch._claim_item(bid, first["id"]))
        self.assertEqual(self.client.post(
            f"/api/creator/batches/{bid}/pause").status_code, 200)
        self.assertFalse(batch._claim_item(bid, second["id"]))
        states = self.client.get(f"/api/creator/batches/{bid}").get_json()["batch"]
        self.assertEqual([item["status"] for item in states["items"]],
                         ["running", "queued"])
        self.assertFalse(batch._claim_item(bid, first["id"]))

    def test_claim_only_one_worker_can_claim_a_video(self):
        response = self.client.post("/api/creator/batches", json=self.payload)
        bid = response.get_json()["batch"]["id"]
        first = response.get_json()["batch"]["items"][0]["id"]
        self.assertTrue(batch._claim_item(bid, first))
        self.assertFalse(batch._claim_item(bid, first))

    def test_app_restart_marks_running_batch_interrupted(self):
        response = self.client.post("/api/creator/batches", json=self.payload)
        bid = response.get_json()["batch"]["id"]
        item = response.get_json()["batch"]["items"][0]
        db.execute("UPDATE creator_batch_items SET status='running' WHERE id=?",
                   (item["id"],))
        db.init_db()
        details = self.client.get(f"/api/creator/batches/{bid}").get_json()["batch"]
        self.assertEqual(details["status"], "interrupted")
        self.assertEqual(details["items"][0]["status"], "interrupted")
        self.assertEqual(self.client.post(
            f"/api/creator/batches/{bid}/retry", json={}).status_code, 202)


if __name__ == "__main__":
    unittest.main()
