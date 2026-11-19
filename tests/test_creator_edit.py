"""Advanced free editing controls: persistence, safety and real video processing."""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from flask import Flask

from clipper import creator_api, creator_edit_api as edit, creator_render_api as render, db


SCRIPT = {"title": "Short documentary", "hook": "Start here",
          "scenes": [{"narration": "An ordinary street scene",
                      "visual_idea": "City street"}],
          "closing": "The end"}


class CreatorEditingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dbpatch = patch.object(db.config, "DB_PATH", Path(self.temp.name) / "edit.sqlite")
        self.datapatch = patch.object(render.config, "DATA_DIR", Path(self.temp.name))
        self.dbpatch.start()
        self.datapatch.start()
        db.init_db()
        app = Flask(__name__)
        app.register_blueprint(creator_api.bp)
        app.register_blueprint(edit.bp)
        self.client = app.test_client()
        response = self.client.post("/api/creator/projects",
                                    json={"topic": "Explore a city street", "target_seconds": 30})
        self.assertEqual(response.status_code, 201)
        self.pid = response.get_json()["project"]["id"]
        self.client.put(f"/api/creator/projects/{self.pid}/script", json=SCRIPT)
        self.endpoint = f"/api/creator/projects/{self.pid}/edit/options"

    def tearDown(self):
        self.datapatch.stop()
        self.dbpatch.stop()
        self.temp.cleanup()

    def test_old_projects_keep_identical_edit_defaults(self):
        result = self.client.get(self.endpoint)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.get_json()["options"], edit.DEFAULTS)
        self.assertEqual(result.get_json()["formats"]["square"]["width"], 1080)

    def test_setting_invalidates_render_but_preserves_narration(self):
        db.execute("UPDATE creator_stages SET status='done' WHERE project_id=? "
                   "AND stage IN ('narration','render')", (self.pid,))
        new = {"format": "landscape", "fit": "contain", "speed": 1.5, "order": "reverse"}
        response = self.client.put(self.endpoint, json=new)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(self.client.get(self.endpoint).get_json()["options"],
                         {**edit.DEFAULTS, **new})
        self.assertEqual(db.query_one("SELECT status FROM creator_stages WHERE project_id=? "
                                       "AND stage='render'",(self.pid,))["status"],"pending")
        self.assertEqual(db.query_one("SELECT status FROM creator_stages WHERE project_id=? "
                                       "AND stage='narration'",(self.pid,))["status"],"done")

    def test_rejects_bad_values_and_running_job(self):
        cases = [
            {"format":"landscape","fit":"cover","speed":100,"order":"script"},
            {"format":"portrait","fit":"bogus","speed":1,"order":"script"},
            {"format":"portrait","fit":"cover","speed":1,"order":"unknown"},
            {"format":"portrait","fit":"cover","speed":True,"order":"script"},
            {"format":"portrait","fit":"cover","speed":1,"order":"script","injected":"x"},
        ]
        for case in cases:
            self.assertEqual(self.client.put(self.endpoint, json=case).status_code,400)
        db.execute("UPDATE creator_projects SET status='rendering' WHERE id=?", (self.pid,))
        self.assertEqual(self.client.put(self.endpoint,json=edit.DEFAULTS).status_code,409)

    def test_audio_mix_controls_are_bounded_and_invalidate_only_render(self):
        for update in (
            {"narration_volume": True},
            {"narration_volume": -0.1},
            {"narration_volume": 2.01},
            {"bgm_volume": -0.1},
            {"bgm_volume": 1.01},
            {"bgm_volume": "0.15"},
        ):
            payload={**edit.DEFAULTS, **update}
            response=self.client.put(self.endpoint, json=payload)
            self.assertEqual(response.status_code, 400, payload)
        db.execute("UPDATE creator_stages SET status='done' WHERE project_id=? "
                   "AND stage IN ('narration','visuals','render')", (self.pid,))
        payload={**edit.DEFAULTS,"narration_volume":1.25,"bgm_volume":0.05}
        response=self.client.put(self.endpoint,json=payload)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["changed"])
        saved=self.client.get(self.endpoint).get_json()["options"]
        self.assertEqual(saved["narration_volume"],1.25)
        self.assertEqual(saved["bgm_volume"],0.05)
        self.assertEqual(db.query_one("SELECT status FROM creator_stages "
            "WHERE project_id=? AND stage='render'", (self.pid,))["status"],"pending")
        self.assertEqual(db.query_one("SELECT status FROM creator_stages "
            "WHERE project_id=? AND stage='narration'", (self.pid,))["status"],"done")
        self.assertEqual(db.query_one("SELECT status FROM creator_stages "
            "WHERE project_id=? AND stage='visuals'", (self.pid,))["status"],"done")
        # No spurious invalidation for an identical write.
        response=self.client.put(self.endpoint,json=payload)
        self.assertFalse(response.get_json()["changed"])

    def test_music_ducking_is_opt_in_and_keeps_original_stages(self):
        self.assertFalse(self.client.get(self.endpoint).get_json()["options"]["music_ducking"])
        for invalid in ("true", 1, None, []):
            response = self.client.put(
                self.endpoint, json={**edit.DEFAULTS, "music_ducking": invalid})
            self.assertEqual(response.status_code, 400)
        db.execute("UPDATE creator_stages SET status='done' WHERE project_id=? "
                   "AND stage IN ('narration','visuals','render')", (self.pid,))
        result = self.client.put(
            self.endpoint, json={**edit.DEFAULTS, "music_ducking": True})
        self.assertEqual(result.status_code, 200)
        self.assertTrue(result.get_json()["changed"])
        self.assertTrue(self.client.get(self.endpoint).get_json()["options"]["music_ducking"])
        rows = db.query("SELECT stage,status FROM creator_stages WHERE project_id=?",
                        (self.pid,))
        statuses = {row["stage"]: row["status"] for row in rows}
        self.assertEqual(statuses["render"], "pending")
        self.assertEqual(statuses["visuals"], "done")
        self.assertEqual(statuses["narration"], "done")

    def test_legacy_four_field_settings_migrate_to_audio_defaults(self):
        legacy={"format":"landscape","fit":"cover","speed":1.5,"order":"script"}
        db.execute("INSERT INTO creator_edit_settings(project_id,options_json) "
                   "VALUES (?,?)", (self.pid,json.dumps(legacy)))
        saved=self.client.get(self.endpoint).get_json()["options"]
        self.assertEqual(saved,{**edit.DEFAULTS, **legacy})

    def test_reordered_footage_does_not_change_voice_slot_order(self):
        slots = ["hook", "scene_0", "scene_1", "closing"]
        self.assertEqual(edit.footage_mapping(slots, 1, "reverse"),
                         {"hook":"closing","scene_0":"scene_1",
                          "scene_1":"scene_0","closing":"hook"})
        self.assertEqual(edit.footage_mapping(slots, 3, "shuffle"),
                         edit.footage_mapping(slots, 3, "shuffle"))
        self.assertEqual(set(edit.footage_mapping(slots, 3, "shuffle").values()),set(slots))

    def test_manual_scene_footage_reuse_keeps_audio_slots(self):
        endpoint=f"/api/creator/projects/{self.pid}/edit/scene-assignments"
        self.assertEqual(self.client.get(endpoint).get_json()["assignments"], {})
        db.execute("UPDATE creator_stages SET status='done' WHERE project_id=? "
                   "AND stage IN ('narration','visuals','render')", (self.pid,))
        response=self.client.put(endpoint, json={"assignments":{"scene_0":"hook",
                                                                   "closing":"hook"}})
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertTrue(response.get_json()["changed"])
        self.assertEqual(self.client.get(endpoint).get_json()["assignments"],
                         {"scene_0":"hook", "closing":"hook"})
        stages={row["stage"]:row["status"] for row in db.query(
            "SELECT stage,status FROM creator_stages WHERE project_id=?", (self.pid,))}
        self.assertEqual(stages["render"], "pending")
        self.assertEqual(stages["visuals"], "done")
        self.assertEqual(stages["narration"], "done")
        self.assertFalse(self.client.put(endpoint, json={
            "assignments":{"scene_0":"hook","closing":"hook"}}).get_json()["changed"])
        for invalid in (
            {"assignments":{"scene_0":"scene_99"}},
            {"assignments":{"unknown":"hook"}},
            {"assignments":{"hook":"../../etc/passwd"}},
            {"assignments":["hook"]},
        ):
            self.assertEqual(self.client.put(endpoint,json=invalid).status_code,400)
        db.execute("UPDATE creator_projects SET status='rendering' WHERE id=?", (self.pid,))
        self.assertEqual(self.client.put(endpoint, json={"assignments":{}}).status_code,409)

    def test_default_footage_processing_is_identity(self):
        source = Path(self.temp.name) / "source.mp4"
        source.write_bytes(b"original")
        self.assertEqual(edit.prepare_footage(self.pid,source,"hook",edit.DEFAULTS),source)

    def test_manifest_uses_format_order_and_keeps_audio(self):
        run_id = "a"*32
        folder = render._root(self.pid)/"narration"/run_id
        folder.mkdir(parents=True)
        clips = {}
        for index, slot in enumerate(["hook","scene_0","closing"]):
            (folder/f"part_{index:02d}.wav").write_bytes(b"prepared")
            media=render._root(self.pid)/"visuals"/f"clip_{slot}.mp4"
            media.parent.mkdir(parents=True,exist_ok=True)
            media.write_bytes(b"media")
            clips[slot]=media.name
        db.execute("UPDATE creator_stages SET status='done',artifact_json=? "
                   "WHERE project_id=? AND stage='visuals'",
                   (json.dumps({"clips":clips}),self.pid))
        db.execute("UPDATE creator_stages SET status='done',artifact_json=? "
                   "WHERE project_id=? AND stage='narration'",
                   (json.dumps({"run_id":run_id,"segments":[
                       {"file":f"part_{i:02d}.wav","duration":1,"text":"words"}
                       for i in range(3)]}),self.pid))
        new={"format":"landscape","fit":"cover","speed":1,"order":"reverse"}
        self.client.put(self.endpoint,json=new)
        spec=render._manifest(self.pid,render._project(self.pid))
        self.assertEqual((spec["settings"]["width"],spec["settings"]["height"]),(1280,720))
        self.assertTrue(spec["scenes"][0]["footage"].endswith("clip_closing.mp4"))
        self.assertTrue(spec["scenes"][0]["audio"].endswith("part_00.wav"))
        self.assertEqual(spec["settings"]["narration_volume"], 1.0)
        self.assertEqual(spec["settings"]["bgm_volume"], 0.15)
        adjusted={**new,"narration_volume":1.5,"bgm_volume":0.4}
        self.client.put(self.endpoint,json=adjusted)
        spec=render._manifest(self.pid,render._project(self.pid))
        self.assertEqual(spec["settings"]["narration_volume"], 1.5)
        self.assertEqual(spec["settings"]["bgm_volume"], 0.4)
        manual=self.client.put(f"/api/creator/projects/{self.pid}/edit/scene-assignments",
                               json={"assignments":{"scene_0":"hook"}})
        self.assertEqual(manual.status_code,200, manual.get_json())
        remapped=render._manifest(self.pid,render._project(self.pid))
        self.assertTrue(remapped["scenes"][1]["footage"].endswith("clip_hook.mp4"))
        self.assertTrue(remapped["scenes"][1]["audio"].endswith("part_01.wav"))
        self.assertFalse(spec["settings"]["music_ducking"])
        adjusted["music_ducking"] = True
        self.client.put(self.endpoint, json=adjusted)
        spec = render._manifest(self.pid, render._project(self.pid))
        self.assertTrue(spec["settings"]["music_ducking"])


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),"FFmpeg unavailable")
class ActualFootagePreprocessingTests(CreatorEditingTests):
    def test_real_ffmpeg_contains_full_frame_and_changes_speed(self):
        folder=Path(self.temp.name)
        source=folder/"original.mp4"
        subprocess.run(["ffmpeg","-hide_banner","-loglevel","error","-y",
                        "-f","lavfi","-i","testsrc2=size=320x180:rate=12:duration=2",
                        "-c:v","libx264","-pix_fmt","yuv420p",str(source)],
                       check=True,timeout=35)
        settings={"format":"landscape","fit":"contain","speed":2.0,"order":"script"}
        prepared=edit.prepare_footage(self.pid,source,"hook",settings)
        self.assertTrue(prepared.is_file())
        self.assertNotEqual(prepared.resolve(),source.resolve())
        probe=subprocess.run(
            ["ffprobe","-v","error","-show_entries","stream=width,height:format=duration",
             "-of","json",str(prepared)],capture_output=True,text=True,check=True,timeout=15)
        info=json.loads(probe.stdout)
        self.assertEqual((info["streams"][0]["width"],info["streams"][0]["height"]),(1280,720))
        self.assertAlmostEqual(float(info["format"]["duration"]),1.0,delta=0.2)
        self.assertEqual(edit.prepare_footage(self.pid,source,"hook",settings),prepared)
        self.assertTrue(source.exists())


if __name__=="__main__":
    unittest.main()
