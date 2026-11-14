"""B-roll renderer safety checks: run python -m unittest discover -s tests."""
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from clipper import broll_render_api as render


class BrollRenderTests(unittest.TestCase):
    def test_reject_invalid_and_overlapping_cutaways(self):
        with patch.object(render.broll_api, "_item", return_value=Path("example.png")):
            with self.assertRaisesRegex(ValueError, "between 1 and 6"):
                render.validate_plan([], 1, 20)
            with self.assertRaisesRegex(ValueError, "fit inside"):
                render.validate_plan([{"asset_id": "a", "start": float("nan"), "end": 4}], 1, 20)
            with self.assertRaisesRegex(ValueError, "must not overlap"):
                render.validate_plan([
                    {"asset_id": "a", "start": 2, "end": 5},
                    {"asset_id": "b", "start": 4, "end": 7},
                ], 1, 20)

    def test_reject_assets_from_other_projects(self):
        with patch.object(render.broll_api, "_item", return_value=None):
            with self.assertRaisesRegex(ValueError, "no longer available"):
                render.validate_plan([{"asset_id": "a", "start": 2, "end": 4}], 15, 20)

    def test_plan_is_sorted_and_uses_separate_files(self):
        with patch.object(render.broll_api, "_item", side_effect=lambda _, aid: Path(aid + ".png")):
            plan = render.validate_plan([
                {"asset_id": "second", "start": 8, "end": 10},
                {"asset_id": "first", "start": 2, "end": 4},
            ], 1, 15)
        self.assertEqual([cut["asset_id"] for cut in plan], ["first", "second"])
        with patch.object(render.video, "_tool", return_value="ffmpeg"):
            cmd = render.build_command(Path("original.mp4"), plan, Path("new.mp4"),
                                       1080, 1920, 15)
        self.assertEqual(cmd[cmd.index("-map") + 1], "[merged2]")
        self.assertEqual(cmd[cmd.index("-t") + 1], "15.000")
        self.assertIn("0:a:0?", cmd)
        self.assertIn("-loop", cmd)
        self.assertEqual(cmd[-1], "new.mp4")

    def test_accepts_video_as_a_silent_overlay(self):
        plan = [{"asset_id": "abc", "start": 3, "end": 6,
                 "path": Path("footage.mp4")}]
        with patch.object(render.video, "_tool", return_value="ffmpeg"):
            cmd = render.build_command(Path("original.mp4"), plan, Path("copy.mp4"),
                                       720, 1280, 10)
        self.assertIn("-stream_loop", cmd)
        self.assertIn("0:a:0?", cmd)
        self.assertNotIn("1:a", " ".join(cmd))


if __name__ == "__main__":
    unittest.main()
