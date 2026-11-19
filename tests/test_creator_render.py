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

    def test_music_removal_invalidates_completed_video_status(self):
        self._add_narration()
        db.execute("UPDATE creator_projects SET status='render_ready' WHERE id=?",
                   (self.pid,))
        db.execute("UPDATE creator_stages SET status='done',artifact_json=? "
                   "WHERE project_id=? AND stage='render'",
                   (json.dumps({"revision_id": "a" * 32}), self.pid))
        response = self.client.delete(f"/api/creator/projects/{self.pid}/music")
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(db.query_one(
            "SELECT status FROM creator_projects WHERE id=?", (self.pid,))["status"],
            "narration_ready")
        row = db.query_one(
            "SELECT status,artifact_json FROM creator_stages "
            "WHERE project_id=? AND stage='render'", (self.pid,))
        self.assertEqual(row["status"], "pending")
        self.assertIsNone(row["artifact_json"])

    def test_unknown_upload_slot_rejected(self):
        response = self.client.post(f"/api/creator/projects/{self.pid}/visuals/scene_99",
                                    data={"video": (io.BytesIO(b"test"), "video.mp4")})
        self.assertEqual(response.status_code, 404)

    def test_scene_preview_requires_saved_project_media(self):
        base = f"/api/creator/projects/{self.pid}/visuals"
        self.assertEqual(self.client.get(base + "/scene_0/preview").status_code, 404)
        self.assertEqual(self.client.get(base + "/scene_99/preview").status_code, 404)
        self.assertEqual(self.client.get(
            "/api/creator/projects/999999/visuals/scene_0/preview").status_code, 404)
        self.assertEqual(self.client.get(base + "/../preview").status_code, 404)

    def test_scene_preview_streams_assigned_media_and_supports_ranges(self):
        base = f"/api/creator/projects/{self.pid}/visuals"
        folder = render._root(self.pid) / "visuals"
        folder.mkdir(parents=True, exist_ok=True)
        name = "clip_" + "c" * 32 + ".mp4"
        source = folder / name
        payload = b"test-saved-scene-footage" * 100
        source.write_bytes(payload)
        db.execute("UPDATE creator_stages SET artifact_json=? WHERE project_id=? "
                   "AND stage='visuals'", (json.dumps({"clips": {"scene_0": name}}), self.pid))
        full = self.client.get(base + "/scene_0/preview")
        self.assertEqual(full.status_code, 200)
        self.assertEqual(full.mimetype, "video/mp4")
        self.assertNotIn("attachment", full.headers.get("Content-Disposition", ""))
        self.assertEqual(full.data, payload)
        ranged = self.client.get(base + "/scene_0/preview",
                                 headers={"Range": "bytes=0-15"})
        self.assertEqual(ranged.status_code, 206)
        self.assertEqual(ranged.data, payload[:16])
        source.unlink()
        self.assertEqual(self.client.get(base + "/scene_0/preview").status_code, 404)

    def test_scene_footage_rejects_symlinked_files_and_unexpected_extensions(self):
        root = render._root(self.pid) / "visuals"
        root.mkdir(parents=True, exist_ok=True)
        elsewhere = Path(self.tmp.name) / "private.mp4"
        elsewhere.write_bytes(b"private fixture" * 100)
        linked = root / ("clip_" + "d" * 32 + ".mp4")
        try:
            linked.symlink_to(elsewhere)
        except (OSError, NotImplementedError):
            self.skipTest("Filesystem does not permit symlink creation.")
        with self.assertRaises(render.ProjectError):
            render._file_for_slot(self.pid, linked.name)
        other = root / ("clip_" + "e" * 32 + ".txt")
        other.write_bytes(b"not video" * 200)
        with self.assertRaises(render.ProjectError):
            render._file_for_slot(self.pid, other.name)
        self.assertEqual(elsewhere.read_bytes(), b"private fixture" * 100)

    def test_scene_preview_rejects_unsafe_saved_filename(self):
        base = f"/api/creator/projects/{self.pid}/visuals/scene_0/preview"
        db.execute("UPDATE creator_stages SET artifact_json=? WHERE project_id=? "
                   "AND stage='visuals'",
                   (json.dumps({"clips": {"scene_0": "../private.mp4"}}), self.pid))
        self.assertEqual(self.client.get(base).status_code, 404)

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

    def test_interrupted_render_recovers_after_restart(self):
        db.execute("UPDATE creator_projects SET status='rendering' WHERE id=?", (self.pid,))
        db.execute("UPDATE creator_stages SET status='running' "
                   "WHERE project_id=? AND stage='render'", (self.pid,))
        db.init_db()
        self.assertEqual(render._project(self.pid)["status"], "narration_ready")
        state = self.client.get(f"/api/creator/projects/{self.pid}/render").get_json()
        self.assertEqual(state["status"], "failed")

    def test_restart_recovers_render_without_saved_narration(self):
        db.execute("UPDATE creator_projects SET status='rendering' WHERE id=?", (self.pid,))
        db.execute("UPDATE creator_stages SET status='running' "
                   "WHERE project_id=? AND stage='render'", (self.pid,))
        db.init_db()
        self.assertEqual(render._project(self.pid)["status"], "script_ready")
        stage = db.query_one(
            "SELECT status FROM creator_stages WHERE project_id=? AND stage='render'",
            (self.pid,))
        self.assertEqual(stage["status"], "failed")

    def test_restart_preserves_prior_successful_narration_during_new_voice_failure(self):
        self._add_narration()
        db.execute("UPDATE creator_projects SET status='generating_narration' WHERE id=?",
                   (self.pid,))
        db.init_db()
        self.assertEqual(render._project(self.pid)["status"], "narration_ready")
        stage = db.query_one(
            "SELECT status FROM creator_stages WHERE project_id=? AND stage='narration'",
            (self.pid,))
        self.assertEqual(stage["status"], "done")

    def test_restart_recovers_narration_without_saved_audio(self):
        db.execute("UPDATE creator_projects SET status='generating_narration' WHERE id=?",
                   (self.pid,))
        db.execute("UPDATE creator_stages SET status='running' "
                   "WHERE project_id=? AND stage='narration'", (self.pid,))
        db.init_db()
        self.assertEqual(render._project(self.pid)["status"], "script_ready")
        stage = db.query_one(
            "SELECT status FROM creator_stages WHERE project_id=? AND stage='narration'",
            (self.pid,))
        self.assertEqual(stage["status"], "failed")

    def test_music_stage_invalidation_and_safety(self):
        bad = self.client.post(f"/api/creator/projects/{self.pid}/music",
                               data={"audio": (io.BytesIO(b"data"), "unsafe.exe")})
        self.assertEqual(bad.status_code, 400)
        self.assertIsNone(self.client.get(
            f"/api/creator/projects/{self.pid}/music").get_json()["music"])
        self.assertEqual(self.client.delete(
            f"/api/creator/projects/{self.pid}/music").status_code, 200)

    def test_manifest_includes_saved_background_music(self):
        self._add_narration()
        clips = {}
        for slot in ("hook", "scene_0", "closing"):
            name = "clip_" + slot + ".mp4"
            path = render._root(self.pid) / "visuals" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"placeholder")
            clips[slot] = name
        db.execute("UPDATE creator_stages SET status='done', artifact_json=? "
                   "WHERE project_id=? AND stage='visuals'",
                   (json.dumps({"clips": clips}), self.pid))
        music = render._root(self.pid) / "music" / "music_test.mp3"
        music.parent.mkdir(parents=True, exist_ok=True)
        music.write_bytes(b"fake-music")
        db.execute("UPDATE creator_stages SET status='done', artifact_json=? "
                   "WHERE project_id=? AND stage='music'",
                   (json.dumps({"filename": music.name, "duration": 4.0}), self.pid))
        manifest = render._manifest(self.pid, render._project(self.pid))
        self.assertEqual(Path(manifest["bgm"]), music)

    def test_verified_final_mp4_preview_and_download(self):
        revision = "b" * 32
        finished = render._root(self.pid) / "finished" / f"finished_{revision}.mp4"
        finished.parent.mkdir(parents=True, exist_ok=True)
        finished.write_bytes(b"not-real-mp4-but-safe-test" * 100)
        db.execute("UPDATE creator_stages SET status='done',artifact_json=? "
                   "WHERE project_id=? AND stage='render'",
                   (json.dumps({"revision_id": revision, "final_name": finished.name}),
                    self.pid))
        endpoint = f"/api/creator/projects/{self.pid}/render"
        status = self.client.get(endpoint).get_json()
        self.assertTrue(status["ready"], status)
        self.assertEqual(status["download_url"], endpoint + "/file")
        download = self.client.get(endpoint + "/file")
        self.assertEqual(download.status_code, 200)
        self.assertIn("attachment", download.headers["Content-Disposition"])
        download.close()
        preview = self.client.get(endpoint + "/file?preview=1",
                                  headers={"Range": "bytes=0-15"})
        self.assertEqual(preview.status_code, 206)
        self.assertNotIn("attachment", preview.headers.get("Content-Disposition", ""))
        self.assertEqual(len(preview.data), 16)
        preview.close()

        finished.unlink()
        missing = self.client.get(endpoint).get_json()
        self.assertFalse(missing["ready"])
        self.assertIsNone(missing["download_url"])
        self.assertEqual(self.client.get(endpoint + "/file").status_code, 404)

    def test_legacy_export_requires_matching_renderer_revision(self):
        revision = "a" * 32
        newer_revision = "b" * 32
        folder = render._root(self.pid) / "mpt_render" / "artifacts" / "fixture"
        folder.mkdir(parents=True, exist_ok=True)
        output = folder / "out.mp4"
        output.write_bytes(b"valid-sized-fixture" * 120)
        original = {"revision_id": revision, "final_name": None}
        with patch.object(render, "_engine") as engine:
            engine.return_value.metadata.return_value = {
                "last_successful_export": {"revision_id": newer_revision,
                                           "path": str(output)}}
            with self.assertRaises(render.ProjectError):
                render._finished_export(self.pid, original)
            engine.return_value.metadata.return_value[
                "last_successful_export"]["revision_id"] = revision
            self.assertEqual(render._finished_export(self.pid, original), output)
            engine.return_value.metadata.return_value[
                "last_successful_export"] = None
            with self.assertRaises(render.ProjectError):
                render._finished_export(self.pid, original)

    def test_invalid_artifact_cannot_escape_export_folder(self):
        db.execute("UPDATE creator_stages SET status='done',artifact_json=? "
                   "WHERE project_id=? AND stage='render'",
                   (json.dumps({"revision_id": "f" * 32,
                                "final_name": "../secret.mp4"}), self.pid))
        endpoint = f"/api/creator/projects/{self.pid}/render"
        self.assertFalse(self.client.get(endpoint).get_json()["ready"])
        self.assertEqual(self.client.get(endpoint + "/file?preview=1").status_code, 404)

    def test_verified_artifact_rejects_size_changes(self):
        revision = "e" * 32
        folder = render._root(self.pid) / "finished"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"finished_{revision}.mp4"
        path.write_bytes(b"fixture" * 300)
        artifact = {"revision_id": revision, "final_name": path.name,
                    "verification": {"bytes": path.stat().st_size}}
        db.execute("UPDATE creator_stages SET status='done',artifact_json=? "
                   "WHERE project_id=? AND stage='render'",
                   (json.dumps(artifact), self.pid))
        endpoint = f"/api/creator/projects/{self.pid}/render"
        self.assertTrue(self.client.get(endpoint).get_json()["ready"])
        path.write_bytes(b"fixture" * 200)
        self.assertFalse(self.client.get(endpoint).get_json()["ready"])
        self.assertEqual(self.client.get(endpoint + "/file").status_code, 404)

    def test_probe_validation_rejects_missing_audio_bad_duration_and_metadata(self):
        output = render._root(self.pid) / "candidate.mp4"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"video-content" * 120)
        good = {"format": {"duration": "3.0"}, "streams": [
            {"codec_type": "video", "codec_name": "h264", "width": 720, "height": 1280},
            {"codec_type": "audio", "codec_name": "aac"}]}
        def probe(data, code=0):
            return type("Result", (), {"returncode": code, "stdout": json.dumps(data)})()
        with patch.object(render.shutil, "which", return_value="ffprobe"), \
             patch.object(render.subprocess, "run", return_value=probe(good)):
            verified = render._verify_export(output, 3)
            self.assertEqual(verified["width"], 720)
            self.assertTrue(verified["has_audio"])
            self.assertEqual(verified["bytes"], output.stat().st_size)
        for bad in (
            {"format": {"duration": "3.0"}, "streams": good["streams"][:1]},
            {"format": {"duration": "12.0"}, "streams": good["streams"]},
            {"format": {"duration": "NaN"}, "streams": good["streams"]},
            {"format": {}, "streams": good["streams"]},
            {"format": {"duration": "3.0"}, "streams": []},
        ):
            with patch.object(render.shutil, "which", return_value="ffprobe"), \
                 patch.object(render.subprocess, "run", return_value=probe(bad)):
                with self.assertRaises(render.ProjectError):
                    render._verify_export(output, 3)

    def test_cancel_requires_active_render(self):
        self.assertEqual(self.client.post(
            f"/api/creator/projects/{self.pid}/render/cancel").status_code, 409)

    def test_ducking_only_changes_final_export_fingerprint(self):
        from clipper.mpt_compat.video_project import RenderSettings
        self.assertFalse(RenderSettings.parse({}).music_ducking)
        with self.assertRaises(ValueError):
            RenderSettings.parse({"music_ducking": 1})
        base = {"settings": {"music_ducking": False},
                "toolchain": {"ffmpeg": "test", "ffprobe": "test"},
                "bgm": {"sha256": "instrumental"},
                "scenes": [{"id": "intro", "audio": {"sha256": "narration"},
                            "footage": {"sha256": "clip"},
                            "narration": "Hello", "duration": 2.0}]}
        from copy import deepcopy
        altered = deepcopy(base)
        altered["settings"]["music_ducking"] = True
        before = {stage.key: stage.fingerprint for stage in stage_graph(base)}
        after = {stage.key: stage.fingerprint for stage in stage_graph(altered)}
        for key in ("intro:audio", "intro:timing", "intro:scene", "assembly"):
            self.assertEqual(before[key], after[key])
        self.assertNotEqual(before["export"], after["export"])

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
    def test_ffmpeg_music_ducking_filter_accepts_speech_sidechain(self):
        """Run the same FFmpeg audio filter used in production without models."""
        command = [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
            "-f", "lavfi", "-i", "sine=frequency=120:duration=1",
            "-filter_complex",
            "[1:a]volume=0.15[music];"
            "[music][0:a]sidechaincompress="
            "threshold=0.025:ratio=8:attack=20:release=350[ducked];"
            "[0:a][ducked]amix=inputs=2:duration=first:normalize=0[a]",
            "-map", "[a]", "-f", "null", "-",
        ]
        completed = subprocess.run(command, capture_output=True,
                                   text=True, timeout=30)
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_real_ffprobe_accepts_valid_mp4_and_rejects_corrupt_file(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            output = root / "verified.mp4"
            command = [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "color=c=blue:s=128x128:r=10:d=2",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-c:a", "aac", str(output),
            ]
            subprocess.run(command, check=True, timeout=40)
            verified = render._verify_export(output, 2.0)
            self.assertTrue(verified["has_audio"])
            self.assertAlmostEqual(verified["duration"], 2.0, delta=0.2)
            corrupt = root / "corrupt.mp4"
            corrupt.write_bytes(b"this is not an mp4" * 100)
            with self.assertRaises(render.ProjectError):
                render._verify_export(corrupt, 2.0)

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
