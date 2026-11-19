"""Offline concurrency tests for the shared local inference coordinator."""
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from clipper import creator_inference as inference


class CreatorInferenceTests(unittest.TestCase):
    def test_heavy_jobs_never_overlap(self):
        entered = threading.Event()
        release = threading.Event()
        second_started = threading.Event()
        order = []

        def first():
            order.append("first_start")
            entered.set()
            self.assertTrue(release.wait(5), "first worker was never released")
            order.append("first_end")

        def second():
            order.append("second_start")
            second_started.set()

        with ThreadPoolExecutor(max_workers=2) as executor:
            a = executor.submit(inference.run_exclusive, first)
            self.assertTrue(entered.wait(5))
            b = executor.submit(inference.run_exclusive, second)
            self.assertFalse(second_started.wait(0.1),
                             "Another heavyweight worker ran while the first was active")
            release.set()
            a.result(timeout=5)
            b.result(timeout=5)
        self.assertEqual(order, ["first_start", "first_end", "second_start"])

    def test_exception_releases_inference_lock(self):
        def fail():
            raise RuntimeError("simulated model failure")

        with self.assertRaisesRegex(RuntimeError, "simulated"):
            inference.run_exclusive(fail)
        self.assertEqual(inference.run_exclusive(lambda value: value + 1, 41), 42)


if __name__ == "__main__":
    unittest.main()
