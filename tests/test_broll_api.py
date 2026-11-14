"""Regression checks for the local B-roll library without FFmpeg or Ollama."""
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from clipper import broll_api


class BrollLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = Flask(__name__)
        self.app.register_blueprint(broll_api.bp)
        self.client = self.app.test_client()
        self.folder_patch = patch.object(broll_api, "MEDIA_ROOT", Path(self.temp.name))
        self.project_patch = patch.object(broll_api, "_project", return_value={"id": 1})
        self.folder_patch.start()
        self.project_patch.start()

    def tearDown(self):
        self.project_patch.stop()
        self.folder_patch.stop()
        self.temp.cleanup()

    def test_upload_list_preview_and_delete(self):
        url = "/api/projects/1/broll"
        result = self.client.post(url, data={"file": (io.BytesIO(b"image-data"), "cutaway.png")})
        self.assertEqual(result.status_code, 201)
        item_id = result.get_json()["id"]
        listing = self.client.get(url).get_json()["items"]
        self.assertEqual(len(listing), 1)
        self.assertEqual(listing[0]["kind"], "image")
        self.assertEqual(self.client.get(f"{url}/{item_id}/file").data, b"image-data")
        self.assertEqual(self.client.delete(f"{url}/{item_id}").status_code, 200)
        self.assertEqual(self.client.get(url).get_json()["items"], [])

    def test_reject_unsupported_and_empty_uploads(self):
        url = "/api/projects/1/broll"
        self.assertEqual(self.client.post(url, data={"file": (io.BytesIO(b"abc"), "x.exe")}).status_code, 400)
        self.assertEqual(self.client.post(url, data={"file": (io.BytesIO(b""), "x.mp4")}).status_code, 400)
        self.assertEqual(self.client.get(f"{url}/../file").status_code, 404)

    def test_reject_missing_project(self):
        with patch.object(broll_api, "_project", return_value=None):
            self.assertEqual(self.client.get("/api/projects/999/broll").status_code, 404)


if __name__ == "__main__":
    unittest.main()
