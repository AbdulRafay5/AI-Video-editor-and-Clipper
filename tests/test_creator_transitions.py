"""Real FFmpeg scene-to-scene transition test using upstream cached scene videos."""
import json
import shutil
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from flask import Flask
from clipper import creator_api, creator_finish_api as finish, creator_render_api as render, db
from clipper.mpt_compat.video_project import VideoProject


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                     "FFmpeg and FFprobe are required")
class CreatorTransitionsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dbp = patch.object(db.config, "DB_PATH", Path(self.tmp.name) / "db.sqlite")
        self.datap = patch.object(render.config, "DATA_DIR", Path(self.tmp.name))
        self.dbp.start()
        self.datap.start()
        db.init_db()
        app = Flask(__name__)
        app.register_blueprint(creator_api.bp)
        app.register_blueprint(finish.bp)
        self.client = app.test_client()
        response = self.client.post("/api/creator/projects",
            json={"topic": "Visual transitions in a short documentary", "target_seconds": 30})
        self.pid = response.get_json()["project"]["id"]

    def tearDown(self):
        self.datap.stop()
        self.dbp.stop()
        self.tmp.cleanup()

    def test_real_slide_scene_transition_preserves_audio_length(self):
        directory = Path(self.tmp.name)
        videos = []
        for index, color in enumerate(("red", "blue")):
            movie = directory / f"scene{index}.mp4"
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                            "-f", "lavfi", "-i", f"color=c={color}:s=128x128:r=10:d=2",
                            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(movie)],
                           check=True, timeout=30)
            videos.append(movie)
        audio = directory / "voice.wav"
        with wave.open(str(audio), "wb") as f:
            f.setnchannels(1);f.setsampwidth(2);f.setframerate(16000)
            f.writeframes(b"\0\0" * (16000*2))
        renderer = render._engine(self.pid)
        spec = renderer.create_revision({
            "project_id": f"creator_{self.pid}",
            "scenes": [
                {"id": f"shot_{i}", "footage": str(path), "audio": str(audio),
                 "narration": "An original narrated scene.", "duration": 2.0}
                for i, path in enumerate(videos)],
            "settings": {"width": 128, "height": 128, "fps": 10},
        })
        base = renderer.render(spec["revision_id"])
        options = self.client.put(
            f"/api/creator/projects/{self.pid}/captions/options",
            json={"captions": False, "fade_edges": False, "transition": "slideleft"})
        self.assertEqual(options.status_code, 200)
        final = finish.finish_project(
            self.pid, Path(base["export"]["path"]), spec["revision_id"],
            directory / "finished")
        self.assertTrue(final.is_file())
        self.assertNotEqual(final.resolve(), Path(base["export"]["path"]).resolve())
        result = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                 "-of", "default=noprint_wrappers=1:nokey=1", str(final)],
                                capture_output=True,text=True,timeout=15,check=True)
        self.assertAlmostEqual(float(result.stdout.strip()), 4.0, delta=0.25)
        # Prove the same scene transition also works with subtitles and edge fades.
        db.execute("UPDATE creator_stages SET status='done', artifact_json=? "
                   "WHERE project_id=? AND stage='narration'",
                   (json.dumps({"duration": 4.0, "segments": [
                       {"start": 0.0, "end": 2.0, "text": "First voiceover scene."},
                       {"start": 2.0, "end": 4.0, "text": "Second voiceover scene."},
                   ]}), self.pid))
        combined = self.client.put(
            f"/api/creator/projects/{self.pid}/captions/options",
            json={"captions": True, "fade_edges": True, "transition": "fade"})
        self.assertEqual(combined.status_code, 200)
        output = finish.finish_project(
            self.pid, Path(base["export"]["path"]), spec["revision_id"], directory / "finished")
        self.assertTrue(output.is_file())
        self.assertGreater(output.stat().st_size, 1024)


if __name__ == "__main__":
    unittest.main()
