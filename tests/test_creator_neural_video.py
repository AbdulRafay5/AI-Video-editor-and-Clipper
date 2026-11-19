"""True text-to-video regression coverage with a fake model and real MP4 clips."""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask
from clipper import creator_api, creator_render_api as creator
from clipper import creator_video_api as video_api, local_text_to_video as model, db

SCRIPT = {
    "title": "Moving forest",
    "hook": "A journey through the forest",
    "scenes": [{"narration": "A deer runs between tall trees.",
                "visual_idea": "A deer running between tall trees, wind moving leaves"}],
    "closing": "Nature is always changing",
}


class NeuralVideoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dbp = patch.object(db.config, "DB_PATH", Path(self.tmp.name) / "test.sqlite")
        self.data = patch.object(creator.config, "DATA_DIR", Path(self.tmp.name))
        self.dbp.start(); self.data.start(); db.init_db()
        app = Flask(__name__)
        app.register_blueprint(creator_api.bp)
        app.register_blueprint(creator.bp)
        app.register_blueprint(video_api.bp)
        self.client = app.test_client()
        response = self.client.post("/api/creator/projects", json={
            "topic": "The forest at sunrise", "target_seconds": 30})
        self.pid = response.get_json()["project"]["id"]
        self.client.put(f"/api/creator/projects/{self.pid}/script", json=SCRIPT)
        self.url = f"/api/creator/projects/{self.pid}/neural-video"

    def tearDown(self):
        self.data.stop(); self.dbp.stop(); self.tmp.cleanup()

    def test_prompts_describe_motion_not_still_images(self):
        prompt = model.prompt_for("scene_0", SCRIPT)
        self.assertIn("deer running", prompt)
        self.assertIn("motion", prompt)
        with self.assertRaises(model.VideoGenerationError):
            model.prompt_for("scene_90", SCRIPT)

    def test_low_memory_preset_and_subject_consistency(self):
        self.assertEqual(model.DEFAULT_FRAMES, 17)
        self.assertEqual(model.DEFAULT_STEPS, 20)
        self.assertEqual(model.FPS, 8)
        topic = "Nami with long orange hair dances on a beach"
        prompt = model.prompt_for("scene_0", SCRIPT, topic)
        self.assertIn(topic, prompt)
        self.assertIn("deer running", prompt)
        self.assertLessEqual(len(prompt), model.MAX_PROMPT)

    def test_consent_invalid_and_dependencies_missing(self):
        self.assertEqual(self.client.post(self.url+"/generate", json={}).status_code,400)
        self.assertEqual(self.client.post(self.url+"/generate",
                                         json={"approve_model_download":1}).status_code,400)
        with patch.object(model, "dependencies_ready", return_value=False):
            self.assertEqual(self.client.post(self.url+"/generate",
                                             json={"approve_model_download":False}).status_code,503)
        self.assertNotEqual(creator._project(self.pid)["status"],"generating_video")

    def test_failed_model_preserves_old_visuals_and_download_flag(self):
        folder = creator._root(self.pid)/"visuals"
        folder.mkdir(parents=True,exist_ok=True)
        old = {"clips":{"hook":"clip_old.mp4"},"sources":{"hook":{"provider":"pexels"}}}
        (folder/"clip_old.mp4").write_bytes(b"old media")
        db.execute("UPDATE creator_stages SET artifact_json=? WHERE project_id=? AND stage='visuals'",
                   (json.dumps(old),self.pid))
        permissions=[]
        class Unavailable:
            def __init__(self, *, allow_download=False):
                permissions.append(allow_download)
            def __enter__(self):
                raise model.VideoGenerationError("CogVideoX model not cached")
            def __exit__(self,*args):
                pass
        with patch.object(model,"LocalCogVideoX",Unavailable), \
             patch("clipper.llm.unload",return_value=None):
            video_api._run(self.pid,False)
        self.assertEqual(permissions,[False])
        stage=db.query_one("SELECT artifact_json FROM creator_stages "
                           "WHERE project_id=? AND stage='visuals'",(self.pid,))
        self.assertEqual(json.loads(stage["artifact_json"]),old)
        self.assertEqual(self.client.get(self.url).get_json()["status"],"blocked")

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),"FFmpeg missing")
    def test_real_mp4s_all_scenes_no_stock_api(self):
        folder=creator._root(self.pid)/"visuals"
        folder.mkdir(parents=True,exist_ok=True)
        sample=Path(self.tmp.name)/"moving_fixture.mp4"
        subprocess.run(["ffmpeg","-hide_banner","-loglevel","error","-y",
                        "-f","lavfi","-i","testsrc=size=320x288:rate=8:duration=2",
                        "-c:v","libx264","-pix_fmt","yuv420p",str(sample)],
                       check=True,timeout=30)
        prompts=[]
        class FakeCogVideo:
            def __init__(self,*,allow_download=False):
                self.approved=allow_download
            def __enter__(self):
                return self
            def __exit__(self,*args):
                return False
            def generate(self,prompt,target,*,steps=30):
                prompts.append(prompt)
                shutil.copyfile(sample,target)
                return model.probe_video(target)
        with patch.object(model,"LocalCogVideoX",FakeCogVideo), \
             patch("clipper.llm.unload",return_value=None):
            video_api._run(self.pid,False)
        state=self.client.get(self.url).get_json()
        self.assertEqual(state["status"],"done",state)
        self.assertEqual(len(prompts),3)
        self.assertTrue(all("The forest at sunrise" in prompt for prompt in prompts))
        visual=self.client.get(f"/api/creator/projects/{self.pid}/visuals").get_json()
        self.assertEqual(len(visual["clips"]),3)
        for slot,name in visual["clips"].items():
            self.assertEqual(visual["sources"][slot]["provider"],"local-cogvideox-2b")
            self.assertGreater(model.probe_video(folder/name)["seconds"],1)

    def test_restart_and_duplicate_job(self):
        db.execute("INSERT INTO creator_video_jobs(project_id,status) VALUES (?,'running')",
                   (self.pid,))
        db.execute("UPDATE creator_projects SET status='generating_video' WHERE id=?",
                   (self.pid,))
        with patch.object(model,"dependencies_ready",return_value=True):
            self.assertEqual(self.client.post(
                self.url+"/generate",json={"approve_model_download":False}).status_code,409)
        db.init_db()
        self.assertEqual(self.client.get(self.url).get_json()["status"],"interrupted")
        self.assertNotEqual(creator._project(self.pid)["status"],"generating_video")

    def test_executor_shutdown_recovers_from_queued_neural_job(self):
        with patch.object(model, "dependencies_ready", return_value=True), \
             patch.object(video_api._executor, "submit",
                          side_effect=RuntimeError("executor shut down")):
            response = self.client.post(
                self.url + "/generate", json={"approve_model_download": False})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(creator._project(self.pid)["status"], "script_ready")
        state = self.client.get(self.url).get_json()
        self.assertEqual(state["status"], "blocked")
        self.assertIn("restart", state["report"]["errors"]["general"])

    def test_start_is_asynchronous_and_validates_boolean(self):
        with patch.object(model,"dependencies_ready",return_value=True), \
             patch.object(video_api._executor,"submit") as submit:
            response=self.client.post(self.url+"/generate",
                                      json={"approve_model_download":False})
        self.assertEqual(response.status_code,202,response.get_data(as_text=True))
        submit.assert_called_once()
        self.assertEqual(self.client.get(self.url).get_json()["status"],"running")


if __name__=="__main__":
    unittest.main()
