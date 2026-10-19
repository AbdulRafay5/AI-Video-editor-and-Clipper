"""Tiny client for a locally running Ollama server. Uses only the standard library."""
import json
import logging
import urllib.error
import urllib.request

import config

log = logging.getLogger("clipper.llm")


class LLMError(Exception):
    """Something went wrong talking to the local language model."""


def _url(path):
    return config.OLLAMA_URL.rstrip("/") + path


def _get(path, timeout=5):
    try:
        with urllib.request.urlopen(_url(path), timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        raise LLMError("Could not reach Ollama. Open the Ollama app (or run 'ollama serve') "
                       f"and try again. ({getattr(e, 'reason', e)})")


def _post(path, payload, timeout):
    req = urllib.request.Request(_url(path), data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:300]
        raise LLMError(f"Ollama returned an error ({e.code}): {body}")
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        raise LLMError("Could not reach Ollama, or it took too long to answer. "
                       f"({getattr(e, 'reason', e)})")


def status():
    """Is Ollama running, and is the configured model downloaded?"""
    wanted = config.LLM_MODEL if ":" in config.LLM_MODEL else config.LLM_MODEL + ":latest"
    try:
        tags = _get("/api/tags")
    except LLMError as e:
        return {"running": False, "model": config.LLM_MODEL, "installed": False, "message": str(e)}
    names = [m.get("name", "") for m in tags.get("models", [])]
    installed = wanted in names
    msg = (f"Ollama is running and '{config.LLM_MODEL}' is installed." if installed else
           f"Ollama is running, but '{config.LLM_MODEL}' is not downloaded. "
           f"Run: ollama pull {config.LLM_MODEL}")
    return {"running": True, "model": config.LLM_MODEL, "installed": installed,
            "available": names, "message": msg}


def chat_json(system, user, schema, num_ctx=None, timeout=300, keep_alive="2m"):
    """Ask the model a question and force the answer to match a JSON schema.
    Thinking mode is switched off so answers are fast. Returns (parsed_dict, stats)."""
    payload = {
        "model": config.LLM_MODEL,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "stream": False,
        "think": False,
        "format": schema,
        "keep_alive": keep_alive,
        "options": {"temperature": 0.2, "num_ctx": num_ctx or config.LLM_CONTEXT, "num_predict": 1500},
    }
    data = _post("/api/chat", payload, timeout)
    content = (data.get("message") or {}).get("content", "")
    try:
        parsed = json.loads(content)
    except ValueError:
        raise LLMError("The model didn't return valid JSON: " + content[:200])
    out_tokens = data.get("eval_count") or 0
    eval_s = (data.get("eval_duration") or 0) / 1e9
    stats = {
        "seconds": round((data.get("total_duration") or 0) / 1e9, 2),
        "prompt_tokens": data.get("prompt_eval_count"),
        "output_tokens": out_tokens,
        "tokens_per_sec": round(out_tokens / eval_s, 1) if eval_s else None,
    }
    return parsed, stats


def unload():
    """Free the model's GPU memory right away (best effort)."""
    try:
        _post("/api/generate", {"model": config.LLM_MODEL, "keep_alive": 0}, timeout=15)
    except LLMError:
        pass