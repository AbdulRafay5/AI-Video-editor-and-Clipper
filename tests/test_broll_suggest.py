"""Pure matching tests; run with: python -m unittest discover -s tests"""
import unittest

from clipper.broll_suggest_api import build_suggestions


class SuggestionTests(unittest.TestCase):
    def test_matches_label_to_spoken_words(self):
        assets = [{"id": "a" * 32, "name": "coffee shop.mp4",
                   "description": "A coffee shop counter"}]
        sentences = [{"start": 5, "end": 7, "text": "We opened a coffee shop last year."}]
        rows = build_suggestions(sentences, assets, 0, 30)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["asset_id"], "a" * 32)
        self.assertEqual(rows[0]["start"], 5)
        self.assertEqual(rows[0]["confidence"], "medium")

    def test_never_invents_a_relevance_match(self):
        assets = [{"id": "b" * 32, "name": "waterfall.png", "description": ""}]
        sentences = [{"start": 8, "end": 10, "text": "We talked about business loans."}]
        self.assertEqual(build_suggestions(sentences, assets, 0, 20), [])

    def test_times_are_relative_to_clip_and_non_overlapping(self):
        assets = [
            {"id": "a" * 32, "name": "coffee shop.mp4", "description": ""},
            {"id": "b" * 32, "name": "coffee mug.png", "description": ""},
        ]
        sentences = [{"start": 62, "end": 64, "text": "Our coffee shop sells coffee."}]
        rows = build_suggestions(sentences, assets, 60, 80)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["start"], 2)
        self.assertLessEqual(rows[0]["end"], 20)

    def test_does_not_place_cutaways_over_the_initial_hook(self):
        assets = [{"id": "a" * 32, "name": "piano.png", "description": ""}]
        sentences = [{"start": 0, "end": .4, "text": "Piano."}]
        self.assertEqual(build_suggestions(sentences, assets, 0, 8), [])


if __name__ == "__main__":
    unittest.main()
