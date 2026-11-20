"""HTTP startup smoke test for the complete local Flask application.

Run: python scripts/smoke_startup.py
No AI requests, downloads, uploads, FFmpeg processing or external APIs.
"""
import json
import sys
import threading
import urllib.request
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

# Direct `python scripts/smoke_startup.py` adds scripts/, not the repository
# root, to sys.path. Resolve the root explicitly on Windows and Linux.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from werkzeug.serving import make_server

import app as application


def get_json(base, endpoint):
    with urllib.request.urlopen(base + endpoint, timeout=12) as response:
        if response.status != 200:
            raise AssertionError(f"{endpoint}: HTTP {response.status}")
        return json.loads(response.read().decode("utf-8"))


def main():
    server = make_server("127.0.0.1", 0, application.app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with closing(urllib.request.urlopen(base + "/", timeout=12)) as response:
            if response.status != 200 or b"html" not in response.read().lower():
                raise AssertionError("Home page did not return HTML")
        projects = get_json(base, "/api/creator/projects")
        if not isinstance(projects.get("projects"), list):
            raise AssertionError("Creator project list is not available")
        # Never start Ollama/model inference in a CI startup smoke test.
        with patch("clipper.llm.status", return_value={
            "running": False, "installed": False, "model": "offline",
            "message": "Not checked in startup smoke test."}):
            diagnostics = get_json(base, "/api/creator/diagnostics")
        if "ready_for_render" not in diagnostics:
            raise AssertionError("Creator diagnostics route was not registered")
        if "optional" not in diagnostics:
            raise AssertionError("Optional dependency diagnostics are missing")
        print("HTTP startup smoke passed: home, Creator projects and diagnostics")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


if __name__ == "__main__":
    main()
