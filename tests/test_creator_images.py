"""Local AI image scene fallback tests without downloading SD-Turbo weights."""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask
from clipper import creator_api, creator_render_api as creator
from clipper import creator_image_api as images, local_imagegen, db


SCRIPT = {
    "title": "Autumn streets",
    "hook": "A walk in the city.",
    "scenes": [{"narration": "Leaves float past a street lamp.",
                "visual_idea": "red leaves on an autumn street"}],
    "closing": "The city is quiet.",
}


class ImageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dbp = patch.object(db.config, "DB_PATH", Path(self.tmp.name) / "db.sqlite")
        self.dp = patch.object(creator.config, "DATA_DIR", Path(self.tmp.name))
        self.dbp.start(); self.dp.start(); db.init_db()
        app = Flask(__name__)
        app.register_blueprint(creator_api.bp)
        app.register_blueprint(creator.bp)
        app.register_blueprint(images.bp)
        self.client = app.test_client()
        response = self.client.post("/api/creator/projects", json={
            "topic": "Beautiful autumn street documentary", "target_seconds": 30})
        self.pid = response.get_json()["project"]["id"]
        self.client.put(f"/api/creator/projects/{self.pid}/script", json=SCRIPT)
        self.path = f"/api/creator/projects/{self.pid}/images"

    def tearDown(self):
        self.dp.stop(); self.dbp.stop(); self.tmp.cleanup()

    def test_prompt_from_scene_and_invalid_slot(self):
        self.assertIn("red leaves", local_imagegen.image_prompt("scene_0", SCRIPT))
        self.assertIn("red leaves", local_imagegen.image_prompt("hook", SCRIPT))
        with self.assertRaises(local_imagegen.ImageGenerationError):
            local_imagegen.image_prompt("private", SCRIPT)

    def test_download_consent_and_dependency_guard(self):
        self.assertEqual(self.client.post(self.path+"/fill", json={}).status_code, 400)
        self.assertEqual(self.client.post(self.path+"/fill",
                                         json={"approve_model_download": "yes"}).status_code,400)
        with patch.object(local_imagegen, "dependencies_ready", return_value=False):
            self.assertEqual(self.client.post(
                self.path+"/fill", json={"approve_model_download": False}).status_code,503)
        self.assertNotEqual(creator._project(self.pid)["status"], "generating_images")

    def test_missing_model_preserves_existing_footage_and_no_download(self):
        folder = creator._root(self.pid)/"visuals"
        folder.mkdir(parents=True, exist_ok=True)
        existing = folder/"clip_original.mp4"
        existing.write_bytes(b"existing footage")
        db.execute("UPDATE creator_stages SET artifact_json=? WHERE project_id=? AND stage='visuals'",
                   (json.dumps({"clips":{"hook": existing.name}}),self.pid))
        def missing_model(prompt, target, *, allow_download=False):
            self.assertFalse(allow_download)
            raise local_imagegen.ImageGenerationError("Image model is not cached.")
        with patch.object(local_imagegen, "generate_image", side_effect=missing_model):
            images._generate(self.pid, False)
        state = self.client.get(self.path).get_json()
        self.assertEqual(state["status"], "blocked")
        self.assertIn("scene_0", state["report"]["errors"])
        self.assertEqual(creator._visuals(self.pid)["hook"], existing.name)
        self.assertTrue(existing.is_file())

    def test_successful_generated_video_fills_only_missing_scene(self):
        folder = creator._root(self.pid)/"visuals"
        folder.mkdir(parents=True, exist_ok=True)
        existing = folder/"clip_original.mp4"
        existing.write_bytes(b"manual footage")
        db.execute("UPDATE creator_stages SET artifact_json=? WHERE project_id=? AND stage='visuals'",
                   (json.dumps({"clips":{"hook": existing.name}}),self.pid))
        def fake_image(prompt, target, **kw):
            target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(b"faux image content")
            return {"model": local_imagegen.MODEL}
        def fake_animation(src,dst,**kw):
            dst.write_bytes(b"faux mp4 content")
            return dst
        with patch.object(local_imagegen, "generate_image", side_effect=fake_image), \
             patch.object(local_imagegen, "animate_still", side_effect=fake_animation):
            images._generate(self.pid, False)
        state = self.client.get(self.path).get_json()
        self.assertEqual(state["status"], "done", state)
        clips = creator._visuals(self.pid)
        self.assertEqual(clips["hook"],existing.name)
        self.assertTrue(clips["scene_0"].endswith(".mp4"))
        self.assertIn("closing",clips)
        stage = db.query_one("SELECT status,artifact_json FROM creator_stages "
                             "WHERE project_id=? AND stage='visuals'", (self.pid,))
        self.assertEqual(stage["status"],"done")
        self.assertEqual(json.loads(stage["artifact_json"])["sources"]["scene_0"]["provider"],
                         "local-sd-turbo")

    def test_busy_job_rejected(self):
        db.execute("UPDATE creator_projects SET status='generating_images' WHERE id=?",
                   (self.pid,))
        with patch.object(local_imagegen,"dependencies_ready",return_value=True):
            result=self.client.post(self.path+"/fill",
                                    json={"approve_model_download":False})
        self.assertEqual(result.status_code,409)

    def test_restart_recovers_job(self):
        db.execute("INSERT INTO creator_image_jobs(project_id,status) "
                   "VALUES (?,'running')",(self.pid,))
        db.execute("UPDATE creator_projects SET status='generating_images' WHERE id=?",
                   (self.pid,))
        db.init_db()
        state = self.client.get(self.path).get_json()
        self.assertEqual(state["status"],"interrupted")
        self.assertNotEqual(creator._project(self.pid)["status"],"generating_images")

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                         "FFmpeg required")
    def test_real_image_animate_mp4_dimensions(self):
        png = Path(self.tmp.name)/"fixture.png"
        # Single-frame PNG from lavfi; the model itself is not downloaded.
        subprocess.run(["ffmpeg","-hide_banner","-loglevel","error","-y",
                        "-f","lavfi","-i","color=c=orange:s=512x512",
                        "-frames:v","1",str(png)],check=True,timeout=30)
        video = Path(self.tmp.name)/"animated.mp4"
        local_imagegen.animate_still(png,video,seconds=1,width=320,height=576,fps=12)
        details=subprocess.run(["ffprobe","-v","error","-select_streams","v:0",
                                "-show_entries","stream=width,height,nb_frames",
                                "-of","json",str(video)],check=True,text=True,
                               capture_output=True,timeout=20)
        info=json.loads(details.stdout)["streams"][0]
        self.assertEqual((info["width"],info["height"]),(320,576))
        self.assertEqual(int(info["nb_frames"]),12)
        self.assertGreater(video.stat().st_size,1024)


if __name__=="__main__":
    unittest.main()
