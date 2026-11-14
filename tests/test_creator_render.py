"""Creator -> MoneyPrinterTurbo render bridge regression tests.

Offline test data only; never calls stock providers, Ollama or paid APIs.
"""
import io
import json
import shutil
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from clipper import creator_api, creator_render_api as render, db
from clipper.mpt_compat.video_project import VideoProject, stage_graph


SCRIPT = {"title": "Test", "hook": "An opening line.",
          "scenes": [{"narration": "The main narration in the video.",
                      "visual_idea": "A landscape"}],
          "closing": "This is the ending."}


class RenderBridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dbpatch = patch.object(db.config, "DB_PATH", Path(self.tmp.name) / "test.sqlite")
        self.dirpatch = patch.object(render.config, "DATA_DIR", Path(self.tmp.name))
        self.dbpatch.start()
        self.dirpatch.start()
        db.init_db()
        app = Flask(__name__)
        app.register_blueprint(creator_api.bp)
        app.register_blueprint(render.bp)
        self.client = app.test_client()
        created = self.client.post("/api/creator/projects", json={
            "topic": "A short original documentary", "target_seconds": 30})
        self.assertEqual(created.status_code, 201)
        self.pid = created.get_json()["project"]["id"]
        saved = self.client.put(f"/api/creator/projects/{self.pid}/script", json=SCRIPT)
        self.assertEqual(saved.status_code, 200)

    def tearDown(self):
        self.dirpatch.stop()
        self.dbpatch.stop()
        self.tmp.cleanup()

    def _add_narration(self):
        run = "a" * 32
        voice_dir = render._root(self.pid) / "narration" / run
        voice_dir.mkdir(parents=True)
        parts = [{"kind": "hook", "text": SCRIPT["hook"]},
                 {"kind": "scene", "text": SCRIPT["scenes"][0]["narration"]},
                 {"kind": "closing", "text": SCRIPT["closing"]}]
        segments = []
        for idx, part in enumerate(parts):
            filename = "part_%02d.wav" % idx
            path = voice_dir / filename
            with wave.open(str(path), "wb") as f:
                f.setnchannels(1)
                f.setsampwidth(2)
                f.setframerate(16000)
                f.writeframes(b"\0\0" * 16000)
            segments.append({**part, "file": filename, "duration": 1.0})
        db.execute("UPDATE creator_stages SET status='done', artifact_json=? "
                   "WHERE project_id=? AND stage='narration'",
                   (json.dumps({"run_id": run, "segments": segments}), self.pid))

    def test_no_render_without_narration_and_footage(self):
        response = self.client.post(f"/api/creator/projects/{self.pid}/render")
        self.assertEqual(response.status_code, 400)
        self.assertIn("Upload", response.get_json()["error"])

    def test_unknown_upload_slot_rejected(self):
        response = self.client.post(f"/api/creator/projects/{self.pid}/visuals/scene_99",
                                    data={"video": (io.BytesIO(b"test"), "video.mp4")})
        self.assertEqual(response.status_code, 404)

    def test_no_upload_during_active_render(self):
        db.execute("UPDATE creator_projects SET status='rendering' WHERE id=?", (self.pid,))
        response = self.client.post(f"/api/creator/projects/{self.pid}/visuals/hook",
                                    data={"video": (io.BytesIO(b"test"), "video.mp4")})
        self.assertEqual(response.status_code, 409)

    def test_visual_manifest_uses_prepared_audio_and_safe_paths(self):
        self._add_narration()
        clips = {}
        for slot in ("hook", "scene_0", "closing"):
            filename = "clip_" + slot + ".mp4"
            path = render._root(self.pid) / "visuals" / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"fake-local-media")
            clips[slot] = filename
        db.execute("UPDATE creator_stages SET status='done', artifact_json=? "
                   "WHERE project_id=? AND stage='visuals'",
                   (json.dumps({"clips": clips}), self.pid))
        manifest = render._manifest(self.pid, render._project(self.pid))
        self.assertEqual(len(manifest["scenes"]), 3)
        self.assertEqual(manifest["project_id"], f"creator_{self.pid}")
        self.assertTrue(all(Path(s["audio"]).is_file() for s in manifest["scenes"]))
        self.assertEqual([s["id"] for s in manifest["scenes"]], ["hook", "scene_0", "closing"])

    def test_upstream_revision_graph_reuses_unaffected_scenes(self):
        base = {"settings": {}, "toolchain": {"ffmpeg": "a", "ffprobe": "b"},
                "bgm": None, "scenes": [
                    {"id": "scene_0", "audio": {"sha256": "abc"}, "footage": {"sha256": "foo"},
                     "narration": "Hello", "duration": 2.0},
                    {"id": "scene_1", "audio": {"sha256": "def"}, "footage": {"sha256": "bar"},
                     "narration": "World", "duration": 3.0},
                ]}
        from copy import deepcopy
        modified = deepcopy(base)
        modified["scenes"][1]["footage"]["sha256"] = "different"
        before = {s.key: s.fingerprint for s in stage_graph(base)}
        after = {s.key: s.fingerprint for s in stage_graph(modified)}
        self.assertEqual(before["scene_0:scene"], after["scene_0:scene"])
        self.assertNotEqual(before["scene_1:scene"], after["scene_1:scene"])
        self.assertNotEqual(before["export"], after["export"])


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                     "FFmpeg is unavailable on test runner")
class UpstreamRenderSmokeTests(unittest.TestCase):
    def test_real_one_scene_video_export_and_cache_reuse(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            footage = root / "footage.mp4"
            audio = root / "voice.wav"
            cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                   "-f", "lavfi", "-i", "color=c=blue:s=128x128:r=10:d=1.2",
                   "-c:v", "libx264", "-pix_fmt", "yuv420p", str(footage)]
            subprocess.run(cmd, check=True, timeout=40)
            with wave.open(str(audio), "wb") as f:
                f.setnchannels(1); f.setsampwidth(2); f.setframerate(16000)
                f.writeframes(b"\0\0" * 16000)
            project = VideoProject(root / "renderer")
            spec = project.create_revision({
                "project_id": "test_render",
                "scenes": [{"id": "scene", "footage": str(footage),
                            "audio": str(audio), "narration": "Hello", "duration": 1.0}],
                "settings": {"width": 128, "height": 128, "fps": 10},
            })
            first = project.render(spec["revision_id"], stage_timeout=60)
            self.assertEqual(first["state"], "succeeded")
            self.assertTrue(Path(first["export"]["path"]).is_file())
            second_spec = project.create_revision({
                "project_id": "test_render",
                "scenes": [{"id": "scene", "footage": str(footage),
                            "audio": str(audio), "narration": "Hello", "duration": 1.0}],
                "settings": {"width": 128, "height": 128, "fps": 10},
            })
            second = project.render(second_spec["revision_id"], stage_timeout=60)
            self.assertEqual(second["state"], "succeeded")
            self.assertTrue(all(x["reused"] for x in second["stages"].values()))


if __name__ == "__main__":
    unittest.main()
