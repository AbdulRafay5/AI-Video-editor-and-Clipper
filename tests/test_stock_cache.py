"""Local cache regression tests; no network or API key needed."""
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from clipper import stock_cache


class StockCacheTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.patch = patch.object(stock_cache, "CACHE_DIR", Path(self.temporary.name))
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.temporary.cleanup()

    def test_hit_and_miss_are_independent_of_case_and_whitespace(self):
        filters = {"video_type": "film"}
        stock_cache.save("pixabay", "coffee shop", filters, [{"id": 42}], now=100)
        self.assertEqual(stock_cache.load("PIXABAY", "  Coffee   Shop  ", filters, now=101),
                         [{"id": 42}])
        self.assertIsNone(stock_cache.load("pixabay", "coffee", filters, now=101))

    def test_expires_after_24_hours(self):
        filters = {"video_type": "film"}
        stock_cache.save("pixabay", "bakery", filters, [{"id": 9}], now=100)
        self.assertIsNone(stock_cache.load("pixabay", "bakery", filters, now=100 + 86400))

    def test_malformed_and_empty_cache_are_not_reused(self):
        filters = {"video_type": "film"}
        stock_cache.save("pixabay", "waterfall", filters, [], now=100)
        self.assertIsNone(stock_cache.load("pixabay", "waterfall", filters, now=101))
        path = stock_cache._path("pixabay", "waterfall", filters)
        path.write_text("{broken", encoding="utf-8")
        self.assertIsNone(stock_cache.load("pixabay", "waterfall", filters, now=101))

    def test_search_runs_only_once_per_cache_key(self):
        calls = []

        def search():
            calls.append(True)
            return [{"id": 7}]

        first = stock_cache.cached_search("pixabay", "coffee shop", {}, search)
        second = stock_cache.cached_search("pixabay", "coffee shop", {}, search)
        self.assertEqual(first, ([{"id": 7}], False))
        self.assertEqual(second, ([{"id": 7}], True))
        self.assertEqual(len(calls), 1)

    def test_failed_search_is_not_cached(self):
        def broken():
            raise RuntimeError("provider is offline")

        with self.assertRaises(RuntimeError):
            stock_cache.cached_search("pixabay", "rainfall", {}, broken)
        self.assertIsNone(stock_cache.load("pixabay", "rainfall", {}))


if __name__ == "__main__":
    unittest.main()
