"""Malformed optional TTS timing metadata must not lose valid narration."""
import json
import tempfile
import unittest
from pathlib import Path

from clipper.creator_narration_api import _sidecar_words


class NarrationSidecarTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "part_00.words.json"

    def tearDown(self):
        self.temp.cleanup()

    def test_missing_and_invalid_sidecars_are_optional(self):
        self.assertEqual(_sidecar_words(self.path, 2.0, 0.0), [])
        for invalid in ("{broken", '{"word": "hi"}', "null"):
            with self.subTest(invalid=invalid):
                self.path.write_text(invalid, encoding="utf-8")
                self.assertEqual(_sidecar_words(self.path, 2.0, 0.0), [])

    def test_invalid_records_skipped_and_valid_timestamps_kept(self):
        self.path.write_text(json.dumps([
            {"word": "Hello", "start": 0.1, "end": 0.45},
            {"word": "oops", "start": "nan", "end": 0.9},
            {"word": "oops", "start": "inf", "end": 0.9},
            {"word": "oops", "start": "bad", "end": 0.9},
            {"word": "outside", "start": 4.0, "end": 5.0},
            None, "wrong shape", {"word": "", "start": 0.2, "end": 0.3},
            {"word": "End", "start": 1.8, "end": 2.2},
            {"word": "after", "start": 2.1, "end": 2.2},
        ]), encoding="utf-8")
        self.assertEqual(_sidecar_words(self.path, 2.0, 3.0), [
            {"word": "Hello", "start": 3.1, "end": 3.45},
            {"word": "End", "start": 4.8, "end": 5.0},
        ])

    def test_oversized_sidecar_is_ignored(self):
        self.path.write_bytes(b"x" * (2 * 1024 * 1024 + 1))
        self.assertEqual(_sidecar_words(self.path, 1.0, 0.0), [])


if __name__ == "__main__":
    unittest.main()
