"""Creator Studio automatic production pipeline regression tests.

No external LLM, paid providers or model downloads. One test renders a real
multi-scene MP4 to prove the autonomous job actually reaches the final export.
"""
import json
import shutil
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from flask import Flask
from clipper import creator_api, creator_narration_api as narration, creator_stock_api as stock, creator_auto_api as auto
from clipper import creator_neural_music_api as neural, local_musicgen
from clipper import creator_image_api as images, local_imagegen
from clipper import creator_video_api as neural_video, local_text_to_video
from clipper import creator_render_api as render, creator_finish_api as finish, db


SCRIPT = {
    "title": "A quiet street film",
    "hook": "The story begins.",
    "scenes": [{"narration": "A person walks along a peaceful street.",
                "visual_idea": "A street scene"}],
    "closing": "And that is the story.",
}


class AutomaticCreatorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dbp = patch.object(db.config, "DB_PATH", Path(self.temp.name) / "db.sqlite")
        self.datap = patch.object(render.config, "DATA_DIR", Path(self.temp.name))
        self.dbp.start(); self.datap.start()
        db.init_db()
        app = Flask(__name__)
        app.register_blueprint(creator_api.bp)
        app.register_blueprint(narration.bp)
        app.register_blueprint(stock.bp)
        app.register_blueprint(images.bp)
        app.register_blueprint(neural_video.bp)
        app.register_blueprint(neural.bp)
        app.register_blueprint(render.bp)
        app.register_blueprint(finish.bp)
        app.register_blueprint(auto.bp)
        self.client = app.test_client()
        self.clientp = patch.object(auto, "_client", side_effect=app.test_client)
        self.clientp.start()
        result = self.client.post("/api/creator/projects", json={
            "topic": "A peaceful documentary about a quiet street", "target_seconds": 30})
        self.assertEqual(result.status_code, 201)
        self.pid = result.get_json()["project"]["id"]
        self.endpoint = f"/api/creator/projects/{self.pid}/auto"

    def tearDown(self):
        self.clientp.stop()
        self.datap.stop(); self.dbp.stop()
        self.temp.cleanup()

    def _save_script(self):
        saved = self.client.put(f"/api/creator/projects/{self.pid}/script", json=SCRIPT)
        self.assertEqual(saved.status_code, 200)

    def _add_prepared_narration_and_footage(self, real_video=False):
        self._save_script()
        root = render._root(self.pid)
        folder = root / "narration" / ("a"*32)
        folder.mkdir(parents=True)
        segments = []
        clips = {}
        for i, (slot, text) in enumerate((
            ("hook", SCRIPT["hook"]),
            ("scene_0", SCRIPT["scenes"][0]["narration"]),
            ("closing", SCRIPT["closing"]),
        )):
            filename = f"part_{i:02d}.wav"
            with wave.open(str(folder / filename), "wb") as wav:
                wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(16000)
                wav.writeframes(b"\0\0" * 16000)
            segments.append({"file": filename, "duration": 1.0, "start": i,
                             "end": i+1.0, "text": text, "kind": slot})
            video = root / "visuals" / f"clip_{i}.mp4"
            video.parent.mkdir(parents=True, exist_ok=True)
            if real_video:
                subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                                "-f", "lavfi", "-i",
                                f"color=c={('red','green','blue')[i]}:s=128x128:r=10:d=1.2",
                                "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video)],
                               check=True, timeout=30)
            else:
                video.write_bytes(b"unused placeholder")
            clips[slot] = video.name
        db.execute("UPDATE creator_stages SET status='done', artifact_json=? "
                   "WHERE project_id=? AND stage='narration'",
                   (json.dumps({"run_id": "a"*32, "duration": 3.0, "segments": segments}),
                    self.pid))
        db.execute("UPDATE creator_stages SET status='done', artifact_json=? "
                   "WHERE project_id=? AND stage='visuals'",
                   (json.dumps({"clips": clips}), self.pid))
        db.execute("UPDATE creator_projects SET status='narration_ready' WHERE id=?",
                   (self.pid,))

    def _start_and_run(self, options):
        with patch.object(auto._executor, "submit") as submit:
            started = self.client.post(self.endpoint + "/start", json=options)
        self.assertEqual(started.status_code, 202, started.get_data(as_text=True))
        submit.assert_called_once()
        auto._automate(self.pid, submit.call_args.args[2])
        return self.client.get(self.endpoint).get_json()

    def test_refuses_unapproved_missing_stock_and_reports_blocker(self):
        self._save_script()
        result = self._start_and_run({"music": "none"})
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["step"], "narration")
        self.assertTrue(result["report"]["error"])
        self.assertNotEqual(result["report"]["error"], "Production stage rejected.")
        self.assertEqual(result["report"]["completed"], ["script"])

    def test_duplicate_running_job_is_rejected(self):
        db.execute("INSERT INTO creator_auto_jobs(project_id,status,step) "
                   "VALUES (?,'running','script')", (self.pid,))
        response = self.client.post(self.endpoint + "/start", json={})
        self.assertEqual(response.status_code, 409)

    def test_ai_retry_reuses_existing_complete_ai_scenes(self):
        self._add_prepared_narration_and_footage()
        from clipper import creator_auto_api as automation
        visuals = db.query_one(
            "SELECT artifact_json FROM creator_stages WHERE project_id=? AND stage='visuals'",
            (self.pid,))
        artifact = json.loads(visuals["artifact_json"])
        slots = tuple(artifact["clips"])
        artifact["sources"] = {
            slot: {"provider": "local-sd-turbo"} for slot in slots}
        db.execute(
            "UPDATE creator_stages SET artifact_json=? WHERE project_id=? AND stage='visuals'",
            (json.dumps(artifact), self.pid))
        self.assertTrue(automation._reusable_ai_scenes(self.pid, slots))
        with patch.object(automation, "_invoke") as invoke, \
             patch.object(automation, "_wait_idle"), \
             patch.object(render, "_music", return_value=None):
            # Missing neural music dependencies stop the job at music;
            # image generation must not be invoked during the retry.
            from clipper import local_musicgen
            with patch.object(local_musicgen, "dependencies_ready", return_value=False):
                status = self._start_and_run({
                    "visual_mode": "ai", "music": "neural"})
        self.assertEqual(status["status"], "blocked", status)
        self.assertEqual(status["step"], "music")
        self.assertFalse(any("/images/fill" in str(call) for call in invoke.call_args_list))
        self.assertTrue(automation._reusable_ai_scenes(self.pid, slots))

    def test_ai_retry_does_not_reuse_missing_or_manual_footage(self):
        self._add_prepared_narration_and_footage()
        slots = ("hook", "scene_0", "closing")
        self.assertFalse(auto._reusable_ai_scenes(self.pid, slots))
        row = db.query_one("SELECT artifact_json FROM creator_stages "
                           "WHERE project_id=? AND stage='visuals'", (self.pid,))
        artifact = json.loads(row["artifact_json"])
        artifact["sources"] = {slot: {"provider": "local-sd-turbo"} for slot in slots}
        db.execute("UPDATE creator_stages SET artifact_json=? "
                   "WHERE project_id=? AND stage='visuals'",
                   (json.dumps(artifact), self.pid))
        self.assertTrue(auto._reusable_ai_scenes(self.pid, slots))
        footage = render._root(self.pid) / "visuals" / artifact["clips"]["hook"]
        footage.unlink()
        self.assertFalse(auto._reusable_ai_scenes(self.pid, slots))

    def test_true_video_mode_rejects_stock_and_still_images(self):
        self.assertEqual(auto.validate({"visual_mode": "neural_video"})["visual_mode"],
                         "neural_video")
        for options in [
            {"visual_mode": "neural_video", "approve_stock_download": True},
            {"visual_mode": "neural_video", "use_ai_images": True},
        ]:
            self.assertEqual(self.client.post(self.endpoint + "/start",
                                              json=options).status_code, 400)

    def test_visual_mode_validation_keeps_stock_and_ai_separate(self):
        self.assertEqual(auto.validate({"visual_mode": "stock"})["visual_mode"], "stock")
        self.assertEqual(auto.validate({"visual_mode": "ai"})["use_ai_images"], True)
        for body in (
            {"visual_mode": "invalid"},
            {"visual_mode": "ai", "approve_stock_download": True},
            {"visual_mode": "stock", "use_ai_images": True},
        ):
            self.assertEqual(self.client.post(
                self.endpoint + "/start", json=body).status_code, 400)
        self.assertEqual(auto.validate({})["visual_mode"], "legacy")

    def test_invalid_requests_and_missing_project(self):
        self.assertEqual(self.client.post(self.endpoint + "/start", json={
            "approve_stock_download": "yes"}).status_code, 400)
        self.assertEqual(self.client.post(self.endpoint + "/start", json={
            "music": "premium"}).status_code, 400)
        self.assertEqual(self.client.get("/api/creator/projects/999999/auto").status_code, 404)

    def test_neural_music_requires_explicit_boolean_model_download_permission(self):
        response = self.client.post(self.endpoint + "/start", json={
            "music": "neural", "approve_model_download": "yes"})
        self.assertEqual(response.status_code, 400)

    def test_neural_music_missing_dependencies_blocks_without_rendering(self):
        self._add_prepared_narration_and_footage()
        with patch.object(local_musicgen, "dependencies_ready", return_value=False):
            status = self._start_and_run({
                "music": "neural", "approve_model_download": False,
                "approve_stock_download": False})
        self.assertEqual(status["status"], "blocked")
        self.assertEqual(status["step"], "music")
        self.assertIn("optional local MusicGen dependencies", status["report"]["error"])
        self.assertFalse(render._music(self.pid))
        self.assertNotEqual(_status := render._project(self.pid)["status"], "render_ready")

    def test_restart_marks_interrupted_job_and_preserves_project(self):
        self._save_script()
        db.execute("INSERT INTO creator_auto_jobs(project_id,status,step) "
                   "VALUES (?,'running','footage')", (self.pid,))
        db.init_db()
        status = self.client.get(self.endpoint).get_json()
        self.assertEqual(status["status"], "interrupted")
        self.assertTrue(render._project(self.pid)["script_json"])

    def test_missing_footage_cannot_pass_without_download_approval(self):
        self._add_prepared_narration_and_footage()
        row = db.query_one("SELECT artifact_json FROM creator_stages "
                           "WHERE project_id=? AND stage='visuals'", (self.pid,))
        saved = json.loads(row["artifact_json"])
        del saved["clips"]["scene_0"]
        db.execute("UPDATE creator_stages SET artifact_json=? WHERE project_id=? AND stage='visuals'",
                   (json.dumps(saved), self.pid))
        status = self._start_and_run({"approve_stock_download": False})
        self.assertEqual(status["status"], "blocked")
        self.assertEqual(status["step"], "footage")
        self.assertIn("Approve stock downloads or enable local AI image generation", status["report"]["error"])

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                         "Real FFmpeg renderer unavailable")
    def test_topic_only_pipeline_renders_using_offline_stage_doubles(self):
        """All stage transitions are real; only external AI/stock/TTS are faked."""
        from clipper import creator_providers
        original_video = Path(self.temp.name) / "stock_source.mp4"
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                        "-f", "lavfi", "-i", "color=c=yellow:s=128x128:r=10:d=1.5",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(original_video)],
                       check=True, timeout=40)

        def fake_speech(text, destination, engine):
            destination.parent.mkdir(parents=True, exist_ok=True)
            with wave.open(str(destination), "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(16000)
                wav.writeframes(b"\0\0" * 16000)
            return 1.0

        counter = [0]
        def fake_stock(provider, query):
            counter[0] += 1
            return [{
                "download_url": f"https://cdn.pixabay.com/video/{counter[0]}.mp4",
                "source_page": "https://pixabay.com/videos/example/",
                "creator": "fixture", "id": str(counter[0]),
                "provider": "pixabay",
            }]

        with patch.object(creator_api.llm, "status", return_value={
                "running": True, "installed": True, "message": "Ready"}), \
             patch.object(creator_api.llm, "chat_json", return_value=(SCRIPT, {})), \
             patch.object(creator_api._executor, "submit", side_effect=lambda fn,*a: fn(*a)), \
             patch.object(narration.local_narration, "select_backend",
                          return_value={"name": "piper", "voice": "fixture"}), \
             patch.object(narration.local_narration, "synthesize", side_effect=fake_speech), \
             patch.object(narration._executor, "submit", side_effect=lambda fn,*a: fn(*a)), \
             patch.object(creator_providers, "configured", return_value=["pixabay"]), \
             patch.object(creator_providers, "search", side_effect=fake_stock), \
             patch.object(stock, "_download",
                          side_effect=lambda url,destination,provider: shutil.copyfile(
                              original_video,destination)), \
             patch.object(stock._executor, "submit", side_effect=lambda fn,*a: fn(*a)), \
             patch.object(render._executor, "submit", side_effect=lambda fn,*a: fn(*a)):
            result = self._start_and_run({
                "approve_stock_download": True, "music": "none",
                "align_captions": False})
        self.assertEqual(result["status"], "done", result)
        self.assertEqual(result["step"], "review")
        self.assertEqual(result["report"]["completed"], [
            "script", "narration", "footage", "music", "captions", "render"])
        video = self.client.get(f"/api/creator/projects/{self.pid}/render/file")
        self.assertEqual(video.status_code, 200)
        self.assertGreater(len(video.data), 1024)
        video.close()
        self.assertEqual(counter[0], 3)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                         "Real FFmpeg renderer unavailable")
    def test_image_fallback_completes_missing_scene_without_stock_keys(self):
        self._add_prepared_narration_and_footage(real_video=True)
        visuals = db.query_one("SELECT artifact_json FROM creator_stages "
                               "WHERE project_id=? AND stage='visuals'", (self.pid,))
        saved = json.loads(visuals["artifact_json"])
        removed = saved["clips"].pop("scene_0")
        db.execute("UPDATE creator_stages SET status='pending',artifact_json=? "
                   "WHERE project_id=? AND stage='visuals'", (json.dumps(saved), self.pid))
        original = render._root(self.pid) / "visuals" / removed

        def fake_image(prompt, target, **kwargs):
            target.write_bytes(b"image created from prompt fixture")
            return {"model": local_imagegen.MODEL}

        def fake_animation(source, dest, **kwargs):
            shutil.copyfile(original, dest)
            return dest

        with patch.object(local_imagegen, "dependencies_ready", return_value=True), \
             patch.object(local_imagegen, "generate_image", side_effect=fake_image), \
             patch.object(local_imagegen, "animate_still", side_effect=fake_animation), \
             patch.object(images._executor, "submit", side_effect=lambda fn,*a: fn(*a)), \
             patch.object(render._executor, "submit", side_effect=lambda fn,*a: fn(*a)), \
             patch("clipper.creator_providers.configured", return_value=[]):
            status = self._start_and_run({
                "approve_stock_download": True, "use_ai_images": True,
                "approve_image_model_download": False, "music": "none"})
        self.assertEqual(status["status"], "done", status)
        self.assertIn("scene_0", render._visuals(self.pid))
        self.assertEqual(self.client.get(f"/api/creator/projects/{self.pid}/render/file").status_code,200)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                         "Real FFmpeg renderer unavailable")
    def test_ai_visual_mode_replaces_all_existing_stock_and_never_calls_stock(self):
        self._add_prepared_narration_and_footage(real_video=True)
        old = render._visuals(self.pid).copy()
        original = render._root(self.pid) / "visuals" / old["scene_0"]
        all_calls = []

        def fake_image(prompt, target, **kwargs):
            target.write_bytes(b"simulated SD-Turbo PNG")
            return {"model": local_imagegen.MODEL}

        def fake_animation(source, target, **kwargs):
            shutil.copyfile(original, target)
            return target

        def no_stock(*args, **kwargs):
            all_calls.append("stock")
            raise AssertionError("AI-only mode must never search stock providers.")

        with patch.object(local_imagegen, "dependencies_ready", return_value=True), \
             patch.object(local_imagegen, "generate_image", side_effect=fake_image), \
             patch.object(local_imagegen, "animate_still", side_effect=fake_animation), \
             patch.object(images._executor, "submit", side_effect=lambda fn,*a: fn(*a)), \
             patch.object(render._executor, "submit", side_effect=lambda fn,*a: fn(*a)), \
             patch("clipper.creator_providers.configured", side_effect=no_stock), \
             patch.object(stock._executor, "submit", side_effect=no_stock):
            result = self._start_and_run({
                "visual_mode": "ai", "approve_image_model_download": False,
                "music": "none"})
        self.assertEqual(result["status"], "done", result)
        self.assertEqual(result["step"], "review")
        self.assertEqual(all_calls, [])
        visuals = render._visuals(self.pid)
        self.assertEqual(set(visuals), set(old))
        for slot in old:
            self.assertNotEqual(visuals[slot], old[slot])
        self.assertEqual(self.client.get(
            f"/api/creator/projects/{self.pid}/render/file").status_code, 200)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                         "Real FFmpeg renderer unavailable")
    def test_true_neural_video_mode_renders_with_generated_motion_clips_only(self):
        self._add_prepared_narration_and_footage(real_video=True)
        old = render._visuals(self.pid).copy()
        source = render._root(self.pid) / "visuals" / old["hook"]
        prompts = []
        class FakeModel:
            def __init__(self, *, allow_download=False):
                self.approved = allow_download
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def generate(self, prompt, target, *, steps=30):
                prompts.append(prompt)
                shutil.copyfile(source, target)
                return {"seconds": 1.2}
        def reject_stock(*args, **kwargs):
            raise AssertionError("No stock operations allowed in neural video mode.")
        with patch.object(local_text_to_video, "dependencies_ready", return_value=True), \
             patch.object(local_text_to_video, "LocalCogVideoX", FakeModel), \
             patch.object(neural_video._executor, "submit", side_effect=lambda fn,*a: fn(*a)), \
             patch.object(render._executor, "submit", side_effect=lambda fn,*a: fn(*a)), \
             patch("clipper.creator_providers.configured", side_effect=reject_stock), \
             patch.object(stock._executor, "submit", side_effect=reject_stock), \
             patch("clipper.llm.unload", return_value=None):
            status = self._start_and_run({
                "visual_mode": "neural_video", "approve_image_model_download": False,
                "music": "none"})
        self.assertEqual(status["status"], "done", status)
        self.assertEqual(status["step"], "review")
        self.assertEqual(len(prompts), len(old))
        for slot, filename in render._visuals(self.pid).items():
            self.assertNotEqual(filename, old[slot])
        final = self.client.get(f"/api/creator/projects/{self.pid}/render/file")
        self.assertEqual(final.status_code,200)
        final.close()

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                         "Real FFmpeg renderer unavailable")
    def test_real_automatic_render_reuses_stages_and_stops_for_approval(self):
        self._add_prepared_narration_and_footage(real_video=True)
        # Rendering runs synchronously inside this test; underlying FFmpeg
        # and MoneyPrinterTurbo caching are real.
        with patch.object(render._executor, "submit", side_effect=lambda fn,*a: fn(*a)):
            status = self._start_and_run({"music": "none",
                                          "approve_stock_download": False,
                                          "align_captions": False})
        self.assertEqual(status["status"], "done", status)
        self.assertEqual(status["step"], "review")
        self.assertEqual(status["report"]["completed"],
                         ["script", "narration", "footage", "music", "captions", "render"])
        response = self.client.get(f"/api/creator/projects/{self.pid}/render/file")
        self.assertEqual(response.status_code, 200)
        self.assertGreater(len(response.data), 1024)
        response.close()


if __name__ == "__main__":
    unittest.main()
