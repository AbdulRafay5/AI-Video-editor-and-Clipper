"""No-cost neural narration engines.

Kokoro: loopback-only, self-hosted OpenAI-compatible /audio/speech endpoint.
Edge TTS: free online Microsoft neural voices; no API key, Internet required.
Both return validated WAVs for the existing per-scene timeline.
"""
import asyncio
import importlib.util
import ipaddress
import os
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
import json
from pathlib import Path

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
EDGE_VOICES = (
    "en-US-AriaNeural", "en-US-GuyNeural", "en-US-JennyNeural",
    "en-US-AndrewNeural", "en-GB-SoniaNeural", "en-GB-RyanNeural",
)
KOKORO_VOICES = (
    "af_heart", "af_bella", "af_nicole", "am_adam",
    "am_michael", "bf_emma", "bf_isabella", "bm_george", "bm_lewis",
)
MAX_AUDIO_BYTES = 25 * 1024 * 1024


class NeuralVoiceError(ValueError):
    pass


def _kokoro_url():
    raw = os.environ.get("KOKORO_URL", "http://127.0.0.1:8880/v1").strip().rstrip("/")
    parsed = urllib.parse.urlsplit(raw)
    host = parsed.hostname
    try:
        valid_host = host == "localhost" or ipaddress.ip_address(host).is_loopback
        port = parsed.port
    except (TypeError, ValueError):
        valid_host, port = False, None
    if (parsed.scheme != "http" or not valid_host or
            parsed.username or parsed.password or not port or
            parsed.path not in ("", "/v1") or parsed.query or parsed.fragment):
        raise NeuralVoiceError("KOKORO_URL must be a loopback HTTP address such as http://127.0.0.1:8880/v1")
    return raw if parsed.path == "/v1" else raw + "/v1"


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise NeuralVoiceError("Local speech server redirected outside the expected endpoint.")


def _kokoro_request(path, payload=None, timeout=3):
    url = _kokoro_url() + path
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"},
                                 method="POST" if body is not None else "GET")
    opener = urllib.request.build_opener(_NoRedirects(), urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=timeout) as resp:
        if payload is None:
            return json.loads(resp.read(65536).decode("utf-8"))
        size = resp.headers.get("Content-Length")
        if size and int(size) > MAX_AUDIO_BYTES:
            raise NeuralVoiceError("Kokoro generated audio exceeds the size limit.")
        content = resp.read(MAX_AUDIO_BYTES + 1)
        if len(content) > MAX_AUDIO_BYTES:
            raise NeuralVoiceError("Kokoro generated audio exceeds the size limit.")
        return content


def kokoro_ready():
    try:
        response = _kokoro_request("/models", timeout=2)
        return isinstance(response, dict)
    except (OSError, ValueError, urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return False


def available_voices():
    if kokoro_ready():
        local = [{"name": "kokoro", "ready": True, "voice": "af_heart",
                  "voices": list(KOKORO_VOICES),
                  "online": False, "description": "Kokoro: natural neural voices, local server, no API key"}]
    else:
        local = [{"name": "kokoro", "ready": False, "voice": "af_heart",
                  "voices": list(KOKORO_VOICES),
                  "online": False, "description": "Start Kokoro-FastAPI locally on port 8880"}]
    edge_installed = importlib.util.find_spec("edge_tts") is not None
    local.append({"name": "edge", "ready": edge_installed,
                  "voice": "en-GB-SoniaNeural", "voices": list(EDGE_VOICES),
                  "online": True, "description": (
                      "Edge neural voices: free, Internet required, no API key"
                      if edge_installed else "Install optional edge-tts package for free online neural voices")})
    return local


def _to_wav(input_file: Path, output_file: Path):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise NeuralVoiceError("FFmpeg is required to convert neural narration to WAV.")
    result = subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
         "-i", str(input_file), "-vn", "-ac", "1", "-ar", "22050",
         "-c:a", "pcm_s16le", str(output_file)],
        capture_output=True, text=True, timeout=180, creationflags=NO_WINDOW)
    if result.returncode:
        raise NeuralVoiceError("Could not decode speech audio: " + result.stderr[-220:])


async def _edge_stream(text, voice, mp3):
    """Capture both Edge audio and authentic word boundaries in 100-ns ticks."""
    import edge_tts
    word_events = []
    total = 0
    with mp3.open("wb") as sink:
        async for event in edge_tts.Communicate(text, voice).stream():
            kind = event.get("type")
            if kind == "audio":
                content = event.get("data")
                if not isinstance(content, (bytes, bytearray)):
                    continue
                total += len(content)
                if total > MAX_AUDIO_BYTES:
                    raise NeuralVoiceError("Edge speech audio exceeded the local size limit.")
                sink.write(content)
            elif kind == "WordBoundary":
                try:
                    start = float(event["offset"])/10000000
                    duration = float(event["duration"])/10000000
                    word = str(event.get("text", "")).strip()
                except (TypeError, ValueError, KeyError):
                    continue
                if len(word_events) < 2500 and word and 0 <= start < start + duration:
                    word_events.append({"word": word[:100],
                                        "start": round(start, 3),
                                        "end": round(start+duration, 3)})
    return word_events


def speak_neural(text: str, destination: Path, selected: dict):
    from .local_narration import wav_duration
    kind = selected["name"]
    voice = selected["voice"]
    valid = KOKORO_VOICES if kind == "kokoro" else EDGE_VOICES if kind == "edge" else ()
    if voice not in valid:
        raise NeuralVoiceError("Unsupported neural voice.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    mp3 = destination.with_suffix(".mp3")
    mp3.unlink(missing_ok=True)
    try:
        if kind == "kokoro":
            try:
                content = _kokoro_request("/audio/speech", {
                    "model": "kokoro", "input": text, "voice": voice,
                    "response_format": "mp3", "speed": 1.0,
                }, timeout=180)
            except (OSError, urllib.error.URLError, TimeoutError) as exc:
                raise NeuralVoiceError("Cannot reach your local Kokoro server.") from exc
            if not content:
                raise NeuralVoiceError("Kokoro returned an empty audio file.")
            mp3.write_bytes(content)
        elif kind == "edge":
            try:
                import edge_tts
            except ImportError as exc:
                raise NeuralVoiceError("Install edge-tts to use free online Edge voices.") from exc
            try:
                words = asyncio.run(_edge_stream(text, voice, mp3))
                if words:
                    destination.with_suffix(".words.json").write_text(
                        json.dumps(words, ensure_ascii=False), encoding="utf-8")
            except Exception as exc:
                raise NeuralVoiceError("Edge TTS could not generate audio. Check Internet access and retry.") from exc
        else:
            raise NeuralVoiceError("Unsupported neural narration engine.")
        if not mp3.is_file() or not 100 < mp3.stat().st_size <= MAX_AUDIO_BYTES:
            raise NeuralVoiceError("Speech service returned unusable audio.")
        _to_wav(mp3, destination)
        return wav_duration(destination)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise NeuralVoiceError("Could not convert neural speech to WAV.") from exc
    finally:
        mp3.unlink(missing_ok=True)
