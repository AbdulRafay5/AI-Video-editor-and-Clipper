"""Safe, offline tests for script-aware B-roll search (no API calls)."""
import unittest
from unittest.mock import patch

from flask import Flask

from clipper import stock_search_api as stock


class StockSearchTests(unittest.TestCase):
    def test_script_order_and_no_hallucinated_times(self):
        sentences = [
            {"id": 3, "start": 15.0, "end": 18.0, "text": "We opened a coffee shop."},
            {"id": 4, "start": 24.0, "end": 28.0, "text": "Then we bought espresso machines."},
        ]
        clip = {"start_time": 10.0, "end_time": 40.0}
        proposals = [
            {"sentence_id": 4, "query": "espresso machines", "reason": "Shows equipment"},
            {"sentence_id": 3, "query": "coffee shop counter", "reason": "Shows the café"},
            {"sentence_id": 99, "query": "alien palace", "reason": "Not in the transcript"},
        ]
        result = stock._validate_shots(proposals, sentences, clip)
        self.assertEqual([s["sentence_id"] for s in result], [3, 4])
        self.assertEqual([s["start"] for s in result], [5.0, 14.0])

    def test_unsafe_queries_and_duplicate_sentences_rejected(self):
        sentences = [{"id": 1, "start": 2, "end": 5, "text": "We bought a car."}]
        clip = {"start_time": 0, "end_time": 20}
        result = stock._validate_shots([
            {"sentence_id": 1, "query": "https://bad.site", "reason": "bad"},
            {"sentence_id": 1, "query": "car showroom", "reason": "Real footage"},
            {"sentence_id": 1, "query": "car factory", "reason": "Duplicate"},
        ], sentences, clip)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["query"], "car showroom")

    def test_pixabay_url_is_restricted(self):
        self.assertTrue(stock._trusted_pixabay_url("https://cdn.pixabay.com/video/123.mp4"))
        self.assertFalse(stock._trusted_pixabay_url("http://cdn.pixabay.com/video/123.mp4"))
        self.assertFalse(stock._trusted_pixabay_url("https://pixabay.com.evil.org/123.mp4"))
        self.assertFalse(stock._trusted_pixabay_url("https://127.0.0.1/video.mp4"))
        self.assertFalse(stock._trusted_pixabay_url("https://pixabay.com:bad/file.mp4"))
        self.assertFalse(stock._trusted_pixabay_url("https://user:password@pixabay.com/file.mp4"))

    def test_pixabay_parser_prefers_medium_file(self):
        data = {"hits": [{
            "id": 123, "user": "creator", "duration": 12, "pageURL": "https://pixabay.com/videos/test-123/",
            "videos": {
                "medium": {"url": "https://cdn.pixabay.com/video/123_medium.mp4",
                           "width": 1280, "height": 720},
                "large": {"url": "https://cdn.pixabay.com/video/123_large.mp4",
                          "width": 3840, "height": 2160},
            }
        }]}
        results = stock._parse_pixabay(data)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["width"], 1280)
        self.assertEqual(results[0]["id"], 123)

    def test_no_api_key_returns_clear_error(self):
        app = Flask(__name__)
        app.register_blueprint(stock.bp)
        with patch.dict("os.environ", {"PIXABAY_API_KEY": ""}):
            response = app.test_client().get("/api/broll/stock/search?q=coffee")
        self.assertEqual(response.status_code, 503)
        self.assertIn("PIXABAY_API_KEY", response.get_json()["error"])


if __name__ == "__main__":
    unittest.main()
