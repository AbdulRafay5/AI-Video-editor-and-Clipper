"""Test actual Creator Studio ASS burn-in/fade output, SRT and route integration."""
import json
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from clipper import creator_api, creator_finish_api as finish, db


SCRIPT = {
    "title": "A short film", "hook": "The story begins.",
    "scenes": [{"narration": "A curious person walks along a quiet street.",
                "visual_idea": "Someone walking outdoors"}],
    "closing": "And the story ends.",
}


class CreatorFinishTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dbpatch = patch.object(db.config, "DB_PATH", Path(self.temp.name) / "db.sqlite")
        self.dbpatch.start()
        db.init_db()
        app = Flask(__name__)
        app.register_blueprint(creator_api.bp)
        app.register_blueprint(finish.bp)
        self.client = app.test_client()
        created = self.client.post("/api/creator/projects", json={
            "topic": "A quiet walk through a city", "target_seconds": 30})
        self.assertEqual(created.status_code, 201)
        self.pid = created.get_json()["project"]["id"]
        saved = self.client.put(f"/api/creator/projects/{self.pid}/script", json=SCRIPT)
        self.assertEqual(saved.status_code, 200)
        self.artifact = {
            "duration": 3.0,
            "segments": [
                {"start": 0.0, "end": 0.8, "text": SCRIPT["hook"]},
                {"start": 0.8, "end": 2.2, "text": SCRIPT["scenes"][0]["narration"]},
                {"start": 2.2, "end": 3.0, "text": SCRIPT["closing"]},
            ],
        }
        db.execute("UPDATE creator_stages SET status='done', artifact_json=? "
                   "WHERE project_id=? AND stage='narration'",
                   (json.dumps(self.artifact), self.pid))

    def tearDown(self):
        self.dbpatch.stop()
        self.temp.cleanup()

    def test_finishing_worker_is_terminated_on_cancellation(self):
        # A harmless sleeping local subprocess stands in for slow FFmpeg.
        # Cancellation must stop the process, not merely discard its MP4.
        start = time.monotonic()
        with self.assertRaisesRegex(finish.FinishError, "cancelled"):
            finish._run_finishing_ffmpeg(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                Path(self.temp.name), lambda: True, timeout=10)
        self.assertLess(time.monotonic() - start, 3)

    def test_caption_options_validation_and_srt(self):
        response = self.client.get(f"/api/creator/projects/{self.pid}/captions/options")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["settings"]["captions"])
        bad = self.client.put(f"/api/creator/projects/{self.pid}/captions/options",
                              json={"captions": "false", "fade_edges": True})
        self.assertEqual(bad.status_code, 400)
        good = self.client.put(f"/api/creator/projects/{self.pid}/captions/options",
                               json={"captions": True, "fade_edges": False})
        self.assertEqual(good.status_code, 200)
        result = self.client.get(f"/api/creator/projects/{self.pid}/captions.srt")
        self.assertEqual(result.status_code, 200)
        self.assertIn(b"00:00:00,000", result.data)
        self.assertIn(b"The story begins", result.data)

    def test_narration_timing_is_approximate_but_bounded(self):
        captions = finish.timed_captions(self.artifact)
        self.assertGreater(len(captions), 0)
        self.assertLessEqual(max(c["end"] for c in captions), 3.001)
        self.assertGreaterEqual(min(c["start"] for c in captions), 0.0)

    def test_options_change_invalidate_previous_render(self):
        db.execute("UPDATE creator_stages SET status='done' WHERE project_id=? AND stage='render'",
                   (self.pid,))
        self.client.put(f"/api/creator/projects/{self.pid}/captions/options",
                        json={"captions": False, "fade_edges": False})
        stage = db.query_one("SELECT status FROM creator_stages WHERE project_id=? "
                             "AND stage='render'", (self.pid,))
        self.assertEqual(stage["status"], "pending")

    def test_caption_change_clears_completed_project_readiness(self):
        db.execute("UPDATE creator_projects SET status='render_ready' WHERE id=?",
                   (self.pid,))
        db.execute("UPDATE creator_stages SET status='done',artifact_json=? "
                   "WHERE project_id=? AND stage='render'",
                   (json.dumps({"revision_id": "a" * 32}), self.pid))
        response = self.client.put(
            f"/api/creator/projects/{self.pid}/captions/options",
            json={"captions": True, "fade_edges": False})
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(db.query_one(
            "SELECT status FROM creator_projects WHERE id=?", (self.pid,))["status"],
            "narration_ready")
        stage = db.query_one(
            "SELECT status,artifact_json FROM creator_stages "
            "WHERE project_id=? AND stage='render'", (self.pid,))
        self.assertEqual(stage["status"], "pending")
        self.assertIsNone(stage["artifact_json"])
        narration = db.query_one(
            "SELECT status FROM creator_stages WHERE project_id=? AND stage='narration'",
            (self.pid,))
        self.assertEqual(narration["status"], "done")

    def test_no_effects_preserves_upstream_export_path(self):
        self.client.put(f"/api/creator/projects/{self.pid}/captions/options",
                        json={"captions": False, "fade_edges": False})
        src = Path(self.temp.name) / "already-rendered.mp4"
        result = finish.finish_project(self.pid, src, "a"*32, Path(self.temp.name) / "final")
        self.assertEqual(result, src)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                     "FFmpeg/FFprobe not installed")
class ActualCaptionRenderTests(CreatorFinishTests):
    def test_ffmpeg_burns_captions_and_intro_outro_fades(self):
        folder = Path(self.temp.name)
        base = folder / "base.mp4"
        subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "color=c=blue:s=720x1280:r=10:d=3",
            "-f", "lavfi", "-i", "anullsrc=channel_layout=mono:sample_rate=16000",
            "-t", "3", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", str(base)], check=True, timeout=70)
        output = finish.finish_project(self.pid, base, "a"*32, folder / "finished")
        self.assertTrue(output.exists())
        self.assertNotEqual(output.resolve(), base.resolve())
        self.assertTrue(base.exists())
        self.assertGreater(output.stat().st_size, 1024)
        probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                                "-show_entries", "stream=codec_name",
                                "-of", "default=noprint_wrappers=1:nokey=1",
                                str(output)], capture_output=True, text=True, timeout=15)
        self.assertEqual(probe.stdout.strip(), "h264")


class AppRegistrationTests(unittest.TestCase):
    def test_dynamic_feature_registration_in_app(self):
        # Ensure the actual Flask app imports, discovers and exposes Creator Studio.
        import app as application
        routes = {rule.rule for rule in application.app.url_map.iter_rules()}
        self.assertIn("/api/creator/projects", routes)
        self.assertIn("/api/creator/projects/<int:pid>/captions/options", routes)
        self.assertIn("/api/creator/projects/<int:pid>/stock/auto-fill", routes)
        response = application.app.test_client().get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"ext_creator.js", response.data)


if __name__ == "__main__":
    unittest.main()
