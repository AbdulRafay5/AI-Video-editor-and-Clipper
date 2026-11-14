"""MoneyPrinterTurbo spring-caption parity without heavyweight MoviePy imports."""
import json
import math
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask
from clipper import captions, creator_api, creator_finish_api as finish
from clipper import creator_subtitle_animation as anim, db


SCRIPT = {"title":"Animation Test","hook":"The opening line.",
          "scenes":[{"narration":"A narrator explains the concept clearly.",
                     "visual_idea":"Real city footage"}],
          "closing":"Thanks for watching."}


class SpringCaptionTests(unittest.TestCase):
    def test_upstream_curve_is_damped_and_ends_at_normal_size(self):
        self.assertAlmostEqual(anim.spring_scale(0), 0.05)
        self.assertEqual(anim.spring_scale(1), 1.0)
        self.assertTrue(all(0.05 <= anim.spring_scale(t / 100) <= 1.35 for t in range(40)))
        self.assertGreater(anim.spring_scale(0.05), 1.0)

    def test_real_ass_caption_events_have_dynamic_spring_transforms(self):
        caps = [{"id": 1, "start": 0.0, "end": 1.0, "text": "Hello there",
                 "words": [{"word":"Hello","start":0,"end":0.5},
                           {"word":"there","start":0.5,"end":1}]}]
        plain, events = captions.build_ass(caps, {"highlight_word":False}, 720, 1280)
        self.assertGreater(events, 0)
        animated = anim.animate_ass(plain, "pop_spring")
        self.assertIn(r"\fscx5", animated)
        self.assertIn(r"\t(", animated)
        self.assertTrue(any("Dialogue:" in line and r"\fscx" in line
                            for line in animated.splitlines()))
        self.assertEqual(anim.animate_ass(plain, "none"), plain)
        self.assertIn(r"\fad(", anim.animate_ass(plain, "fade"))

    def test_short_cue_never_gets_animation_past_end(self):
        self.assertEqual(anim.ass_prefix("pop_spring", 0.01), "")
        with self.assertRaises(ValueError):
            anim.ass_prefix("unsupported", 1)


class SettingsAndRenderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dbpatch = patch.object(db.config, "DB_PATH", Path(self.temp.name)/"test.sqlite")
        self.dbpatch.start()
        db.init_db()
        app = Flask(__name__)
        app.register_blueprint(creator_api.bp)
        app.register_blueprint(finish.bp)
        self.client = app.test_client()
        created = self.client.post("/api/creator/projects", json={
            "topic":"Animation effects in a documentary", "target_seconds":30})
        self.pid = created.get_json()["project"]["id"]
        response = self.client.put(f"/api/creator/projects/{self.pid}/script", json=SCRIPT)
        self.assertEqual(response.status_code, 200)
        self.narration = {"duration": 3.0,"segments":[
            {"start":0,"end":0.9,"text":SCRIPT["hook"]},
            {"start":0.9,"end":2.2,"text":SCRIPT["scenes"][0]["narration"]},
            {"start":2.2,"end":3.0,"text":SCRIPT["closing"]},
        ]}
        db.execute("UPDATE creator_stages SET status='done', artifact_json=? "
                   "WHERE project_id=? AND stage='narration'",
                   (json.dumps(self.narration),self.pid))

    def tearDown(self):
        self.dbpatch.stop()
        self.temp.cleanup()

    def test_settings_reject_bad_colours_and_animation(self):
        path = f"/api/creator/projects/{self.pid}/captions/options"
        self.assertEqual(self.client.put(path, json={
            "captions":True,"fade_edges":False,"animation":"evil"}).status_code,400)
        self.assertEqual(self.client.put(path, json={
            "captions":True,"fade_edges":False,"text_color":"red,drawbox=x=0"}).status_code,400)
        self.assertEqual(self.client.put(path, json={
            "captions":True,"fade_edges":False,"size_pct":100}).status_code,400)

    def test_persist_style_without_invalidating_existing_timing(self):
        path = f"/api/creator/projects/{self.pid}/captions/options"
        db.execute("UPDATE creator_stages SET artifact_json=? WHERE project_id=? AND stage='captions'",
                   (json.dumps({"aligned_words":[{"word":"Hi","start":0.1,"end":0.5}],
                                "narration_run_id":"abc"}), self.pid))
        response = self.client.put(path, json={
            "captions":True,"fade_edges":False,"animation":"pop_spring",
            "position":"middle","size_pct":7,"text_color":"#eeffdd",
            "highlight_color":"#abcdef","highlight_word":True})
        self.assertEqual(response.status_code,200,response.get_json())
        saved = json.loads(db.query_one("SELECT artifact_json FROM creator_stages "
                                       "WHERE project_id=? AND stage='captions'",
                                       (self.pid,))["artifact_json"])
        self.assertEqual(saved["animation"],"pop_spring")
        self.assertEqual(saved["position"],"middle")
        self.assertEqual(len(saved["aligned_words"]),1)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),"FFmpeg unavailable")
    def test_real_ffmpeg_burns_animated_ass(self):
        p = Path(self.temp.name)
        base = p/"base.mp4"
        subprocess.run(["ffmpeg","-hide_banner","-loglevel","error","-y",
                        "-f","lavfi","-i","color=c=blue:s=720x1280:r=12:d=3",
                        "-f","lavfi","-i","anullsrc=channel_layout=mono:sample_rate=16000",
                        "-t","3","-c:v","libx264","-pix_fmt","yuv420p",
                        "-c:a","aac",str(base)],check=True,timeout=80)
        setres = self.client.put(f"/api/creator/projects/{self.pid}/captions/options",
            json={"captions":True,"fade_edges":False,"animation":"pop_spring",
                  "highlight_word":True,"position":"middle","size_pct":7,
                  "text_color":"#ffffff","highlight_color":"#ffd400"})
        self.assertEqual(setres.status_code,200)
        out = finish.finish_project(self.pid,base,"a"*32,p/"finished")
        self.assertTrue(out.exists())
        self.assertGreater(out.stat().st_size,1024)
        probe=subprocess.run(["ffprobe","-v","error","-select_streams","v:0",
                              "-show_entries","stream=codec_name",
                              "-of","default=noprint_wrappers=1:nokey=1",
                              str(out)],capture_output=True,text=True,timeout=20,check=True)
        self.assertEqual(probe.stdout.strip(),"h264")


if __name__=="__main__":
    unittest.main()
