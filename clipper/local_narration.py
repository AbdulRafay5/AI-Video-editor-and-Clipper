"""Free narration backends: local Kokoro/Piper/Windows speech or online Edge TTS."""
import os
import shutil
import subprocess
import sys
import wave
from pathlib import Path

import config

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class NarrationError(ValueError):
    pass


def backend():
    piper = shutil.which("piper")
    model = os.environ.get("PIPER_MODEL", "").strip()
    if piper and model and Path(model).is_file():
        return {"name": "piper", "ready": True, "voice": Path(model).name,
                "description": "Offline Piper neural voice"}
    if sys.platform == "win32" and shutil.which("powershell"):
        return {"name": "windows-sapi", "ready": True, "voice": "Windows default",
                "description": "Built-in offline Windows speech voice"}
    return {"name": "none", "ready": False, "voice": None,
            "description": "Windows speech is unavailable. Install Piper and set PIPER_MODEL to an existing local .onnx voice file."}


def available_backends():
    """Expose all voice choices; prefer natural local Kokoro when it is running."""
    from . import neural_narration
    choices = neural_narration.available_voices()
    piper = shutil.which("piper")
    model = os.environ.get("PIPER_MODEL", "").strip()
    if piper and model and Path(model).is_file():
        choices.append({"name": "piper", "ready": True, "voice": Path(model).name,
                        "voices": [Path(model).name], "online": False,
                        "description": "Local Piper voice"})
    if sys.platform == "win32" and shutil.which("powershell"):
        choices.append({"name": "windows-sapi", "ready": True, "voice": "Windows default",
                        "voices": ["Windows default"], "online": False,
                        "description": "Windows built-in offline speech"})
    return choices


def select_backend(name=None, voice=None):
    choices = available_backends()
    if name is None:
        selected = next((c for c in choices if c["ready"] and c["name"] == "kokoro"), None)
        if selected is None:
            selected = next((c for c in choices if c["ready"] and not c["online"]), None)
        if selected is None:
            selected = next((c for c in choices if c["ready"]), None)
    else:
        selected = next((c for c in choices if c["name"] == name), None)
    if not selected:
        raise NarrationError("No matching voice engine. Install Kokoro or Edge TTS.")
    if not selected["ready"]:
        raise NarrationError(selected["description"])
    value = selected["voice"] if voice is None else voice
    if not isinstance(value, str) or value not in selected["voices"]:
        raise NarrationError("That voice is not available for the selected engine.")
    return {**selected, "voice": value}


def wav_duration(path: Path) -> float:
    try:
        with wave.open(str(path), "rb") as sound:
            if sound.getnchannels() < 1 or sound.getframerate() <= 0:
                raise NarrationError("Invalid narration WAV.")
            duration = sound.getnframes() / sound.getframerate()
    except (OSError, EOFError, wave.Error) as exc:
        raise NarrationError("Narration engine produced unreadable audio.") from exc
    if not 0.05 < duration < 900:
        raise NarrationError("Narration engine produced an invalid audio duration.")
    return round(duration, 3)


def _ps_quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def synthesize(text: str, target: Path, selected: dict | None = None):
    """Produce one real, validated local WAV file from scene dialogue."""
    if not isinstance(text, str) or not 1 <= len(text.strip()) <= 2000:
        raise NarrationError("Narration must be 1–2000 characters.")
    selected = selected or backend()
    if not selected["ready"]:
        raise NarrationError(selected["description"])
    target.parent.mkdir(parents=True, exist_ok=True)
    target.unlink(missing_ok=True)
    if selected["name"] in ("kokoro", "edge"):
        from . import neural_narration
        try:
            return neural_narration.speak_neural(text.strip(), target, selected)
        except neural_narration.NeuralVoiceError as exc:
            raise NarrationError(str(exc)) from exc
    if selected["name"] == "piper":
        model = Path(os.environ["PIPER_MODEL"]).resolve()
        command = ["piper", "--model", str(model), "--output_file", str(target)]
        stdin = text.strip()
    elif selected["name"] == "windows-sapi":
        if sys.platform != "win32":
            raise NarrationError("Windows speech synthesis is only available on Windows.")
        # The PowerShell script reads user dialogue from a local UTF-8 file, never as code.
        source = target.with_suffix(".txt")
        source.write_text(text.strip(), encoding="utf-8")
        script = (
            "Add-Type -AssemblyName System.Speech; "
            "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            "$s.SetOutputToWaveFile(" + _ps_quote(target) + "); "
            "try { $s.Speak([System.IO.File]::ReadAllText("
            + _ps_quote(source) + ",[System.Text.Encoding]::UTF8)) } "
            "finally { $s.Dispose() }"
        )
        command = ["powershell", "-NoProfile", "-NonInteractive",
                   "-EncodedCommand", __import__("base64").b64encode(script.encode("utf-16le")).decode("ascii")]
        stdin = None
    else:
        raise NarrationError("Unsupported local narration engine.")
    try:
        completed = subprocess.run(
            command, input=stdin, text=True, capture_output=True,
            timeout=180, creationflags=NO_WINDOW, check=False)
        if completed.returncode != 0:
            raise NarrationError("Offline speech engine failed: " + completed.stderr[-250:])
        return wav_duration(target)
    except subprocess.TimeoutExpired as exc:
        raise NarrationError("Offline speech generation timed out.") from exc
    except OSError as exc:
        raise NarrationError("Offline speech engine could not be started.") from exc
    finally:
        target.with_suffix(".txt").unlink(missing_ok=True)


def combine(inputs: list[Path], output: Path):
    """Concatenate generated scene WAV files into a single narration track."""
    if not inputs:
        raise NarrationError("No narration segments to join.")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise NarrationError("FFmpeg is required to combine narration audio.")
    output.parent.mkdir(parents=True, exist_ok=True)
    list_file = output.with_suffix(".list")
    # All input names are server-generated, not influenced by user-supplied paths.
    list_file.write_text("".join("file '" + p.resolve().as_posix().replace("'", "'\\''") + "'\n"
                                 for p in inputs), encoding="utf-8")
    try:
        command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                   "-f", "concat", "-safe", "0", "-i", str(list_file),
                   "-ac", "1", "-ar", "22050", "-c:a", "pcm_s16le", str(output)]
        result = subprocess.run(command, capture_output=True, text=True,
                                timeout=180, creationflags=NO_WINDOW)
        if result.returncode != 0:
            raise NarrationError("FFmpeg couldn't join narration audio: " + result.stderr[-250:])
        return wav_duration(output)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise NarrationError("Could not finish joining narration.") from exc
    finally:
        list_file.unlink(missing_ok=True)
