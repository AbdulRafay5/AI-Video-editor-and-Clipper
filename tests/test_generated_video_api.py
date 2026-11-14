"""Offline tests for optional paid video generation; never contact external services."""
import unittest
from unittest.mock import patch

from flask import Flask
from clipper import generated_video_api as generator


class GeneratedVideoTests(unittest.TestCase):
    def setUp(self):
        app = Flask(__name__)
        app.register_blueprint(generator.bp)
        self.client = app.test_client()

    def test_paid_generation_requires_explicit_approval(self):
        payload = {"prompt": "Realistic footage of a busy coffee shop counter",
                   "duration": 4, "aspect_ratio": "9:16",
                   "approve_paid_generation": False}
        with patch.object(generator, "_read_json") as remote:
            result = self.client.post("/api/video-generation/jobs", json=payload)
        self.assertEqual(result.status_code, 400)
        remote.assert_not_called()

    def test_rejects_invalid_duration(self):
        payload = {"prompt": "A cinematic mountain landscape at sunrise",
                   "duration": 20, "aspect_ratio": "9:16",
                   "approve_paid_generation": True}
        with patch.object(generator, "_read_json") as remote:
            result = self.client.post("/api/video-generation/jobs", json=payload)
        self.assertEqual(result.status_code, 400)
        remote.assert_not_called()

    def test_no_key_means_no_submission(self):
        payload = {"prompt": "A documentary shot of a coastal town",
                   "duration": 5, "aspect_ratio": "16:9",
                   "approve_paid_generation": True}
        with patch.dict("os.environ", {"WAVESPEED_API_KEY": ""}):
            with patch.object(generator, "_read_json") as remote:
                result = self.client.post("/api/video-generation/jobs", json=payload)
        self.assertEqual(result.status_code, 400)
        remote.assert_not_called()

    def test_one_approved_request_submits_exactly_once(self):
        payload = {"prompt": "Real handheld footage of a busy subway station",
                   "duration": 6, "aspect_ratio": "9:16",
                   "approve_paid_generation": True}
        with patch.dict("os.environ", {"WAVESPEED_API_KEY": "fake-test-key"}):
            with patch.object(generator, "_read_json",
                              return_value={"code": 200, "data": {"id": "prediction_123"}}) as remote:
                result = self.client.post("/api/video-generation/jobs", json=payload)
        self.assertEqual(result.status_code, 202)
        self.assertEqual(remote.call_count, 1)
        self.assertEqual(remote.call_args.args[2], {
            "prompt": payload["prompt"], "duration": 6, "aspect_ratio": "9:16"})

    def test_submission_failure_is_not_retried(self):
        payload = {"prompt": "A slow realistic documentary shot of a bookstore",
                   "duration": 4, "aspect_ratio": "9:16",
                   "approve_paid_generation": True}
        with patch.dict("os.environ", {"WAVESPEED_API_KEY": "fake-test-key"}):
            with patch.object(generator, "_read_json", side_effect=TimeoutError()) as remote:
                response = self.client.post("/api/video-generation/jobs", json=payload)
        self.assertEqual(response.status_code, 502)
        self.assertEqual(remote.call_count, 1)
        self.assertIn("status unknown", response.get_json()["error"].lower())


if __name__ == "__main__":
    unittest.main()
