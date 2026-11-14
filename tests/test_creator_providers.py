"""Free provider metadata and URL safety tests, no network access."""
import unittest
from unittest.mock import patch
from clipper import creator_providers as p


class FreeProviderTests(unittest.TestCase):
    def test_hostnames_and_redirects_restricted(self):
        self.assertTrue(p.allowed_url("pexels", "https://videos.pexels.com/video-files/1/a.mp4"))
        self.assertTrue(p.allowed_url("coverr", "https://cdn.coverr.co/videos/test.mp4"))
        self.assertTrue(p.allowed_url("pixabay", "https://cdn.pixabay.com/video/test.mp4"))
        for bad in (
            "http://cdn.pixabay.com/test.mp4",
            "https://pixabay.com.evil.com/test.mp4",
            "https://evil.example/test.mp4",
            "https://pixabay.com@evil.example/test.mp4",
        ):
            self.assertFalse(p.allowed_url("pixabay", bad))
        redirect = p.ProviderOnlyRedirects("pexels")
        with self.assertRaises(ValueError):
            redirect.redirect_request(None, None, 302, "redirect", {}, "https://evil.example/video.mp4")

    def test_pexels_prefers_portrait_smallest_suitable(self):
        data = {"videos": [{"id": 1, "duration": 6, "url": "https://www.pexels.com/video/1/",
            "video_files": [
                {"width": 1920, "height": 1080, "link": "https://videos.pexels.com/video-files/1/large.mp4"},
                {"width": 720, "height": 1280, "link": "https://videos.pexels.com/video-files/1/portrait.mp4"},
                {"width": 400, "height": 720, "link": "https://videos.pexels.com/video-files/1/small.mp4"},
            ]}]}
        result = p.parse("pexels", data)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["provider"], "pexels")
        self.assertIn("small.mp4", result[0]["download_url"])

    def test_coverr_picks_verified_download(self):
        data = {"hits": [{"id": "9", "duration": "8.5", "max_width": 1080,
            "max_height": 1920, "urls": {"mp4_download": "https://cdn.coverr.co/videos/a.mp4"}}]}
        result = p.parse("coverr", data)
        self.assertEqual(len(result), 1)
        self.assertTrue(result[0]["portrait"])

    def test_no_cost_keys_are_optional(self):
        with patch.dict("os.environ", {"PIXABAY_API_KEY": "", "PEXELS_API_KEY": "free", "COVERR_API_KEY": ""}):
            self.assertEqual(p.configured(), ["pexels"])

    def test_malformed_media_records_skipped(self):
        result = p.parse("pexels", {"videos": [
            {"duration": 0, "video_files": []},
            {"duration": 9, "video_files": [{"link": "https://evil.example/1.mp4",
                                            "width": 720, "height": 1280}]},
        ]})
        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
