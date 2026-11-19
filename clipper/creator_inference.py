"""Process-wide coordination for memory-heavy local Creator Studio jobs.

Separate feature executors must not load Ollama, diffusion or MusicGen
simultaneously on a laptop GPU. Waiting callers keep their existing job
status; the lock is released automatically on success or exceptions.
"""
import threading

_inference_lock = threading.Lock()


def run_exclusive(worker, *args, **kwargs):
    """Serialize memory-heavy inference across Creator Studio projects."""
    with _inference_lock:
        return worker(*args, **kwargs)
