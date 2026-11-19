"""Optional, local text-conditioned neural soundtrack generation with MusicGen Small.

Model/processor are loaded from Hugging Face only after explicit download
permission. The UI/API never sends the prompt or audio to a paid inference
provider. This module intentionally does not import torch/transformers at startup.

Model: facebook/musicgen-small (300M parameters); use is subject to its own
model license. 6GB VRAM systems may fall back to CPU depending on available VRAM.
"""
import importlib.util
import math
import wave
from pathlib import Path

MODEL_ID = "facebook/musicgen-small"
MAX_SECONDS = 180
SAMPLE_SECONDS = 8
MAX_PROMPT = 500
MIN_TARGET = 6


class MusicGenError(RuntimeError):
    pass


def dependencies_ready():
    return bool(importlib.util.find_spec("torch") and
                importlib.util.find_spec("transformers") and
                importlib.util.find_spec("numpy"))


def build_prompt(topic, script, mood="cinematic", custom=""):
    """Create short grounded text-conditioning; never claim to analyse footage."""
    if not isinstance(topic, str):
        topic = ""
    if not isinstance(custom, str) or len(custom) > MAX_PROMPT:
        raise MusicGenError("Custom music prompt must be under 500 characters.")
    if custom.strip():
        return custom.strip()
    styles = {
        "cinematic": "cinematic instrumental score, expressive orchestral atmosphere",
        "calm": "warm ambient instrumental soundtrack, gentle piano and soft pads",
        "uplifting": "uplifting instrumental, bright guitar and light rhythmic percussion",
        "suspenseful": "restrained suspense instrumental, low atmospheric pulses and strings",
        "energetic": "energetic instrumental, punchy drums and synthesizer rhythm",
        "ambient": "minimal ambient instrumental, smooth evolving textures",
    }
    if mood not in styles:
        raise MusicGenError("Unknown soundtrack mood.")
    scene = ""
    if isinstance(script, dict):
        scenes = script.get("scenes")
        if isinstance(scenes, list):
            scene = " ".join(str(x.get("visual_idea", ""))[:90] for x in scenes[:3]
                             if isinstance(x, dict))
    context = (topic.strip()[:100] + " " + scene[:160]).strip()
    text = styles[mood] + ", no vocals or spoken words"
    if context:
        text += ", background music for a video about " + context
    return text[:MAX_PROMPT]


def soundtrack_seconds(project, narration):
    """Match spoken timeline if present; otherwise use project target length."""
    if isinstance(narration, dict):
        measured = narration.get("duration")
        if type(measured) in (int, float) and math.isfinite(measured) and measured > 0:
            return min(MAX_SECONDS, max(MIN_TARGET, int(math.ceil(measured))))
    desired = project["target_seconds"]
    if type(desired) is not int or desired <= 0:
        raise MusicGenError("Invalid target soundtrack length.")
    return min(MAX_SECONDS, max(MIN_TARGET, desired))


def _seamless_repeat(mono, target_frames, sample_rate, np):
    """Repeat a neural generation with 200ms crossfades and endpoint fades."""
    mono = np.asarray(mono, dtype=np.float32).reshape(-1)
    if mono.size < sample_rate:
        raise MusicGenError("MusicGen returned too little audio.")
    if not np.all(np.isfinite(mono)):
        raise MusicGenError("MusicGen returned invalid audio.")
    crossfade = min(int(sample_rate * 0.2), mono.size // 10)
    step = mono.size - crossfade
    if step <= 0:
        raise MusicGenError("Neural audio sample is too short.")
    output = np.zeros(target_frames, dtype=np.float32)
    start = 0
    while start < target_frames:
        take = min(mono.size, target_frames - start)
        if start == 0:
            output[:take] = mono[:take]
        else:
            overlap = min(crossfade, take)
            if overlap:
                weight = np.linspace(0, 1, overlap, endpoint=False, dtype=np.float32)
                output[start:start+overlap] = (
                    output[start:start+overlap] * (1-weight) +
                    mono[:overlap] * weight
                )
            if take > overlap:
                output[start+overlap:start+take] = mono[overlap:take]
        start += step
    fade = min(int(0.5 * sample_rate), target_frames // 4)
    if fade > 0:
        output[:fade] *= np.linspace(0, 1, fade, dtype=np.float32)
        output[-fade:] *= np.linspace(1, 0, fade, dtype=np.float32)
    return np.clip(output, -0.95, 0.95)


def generate(output, prompt, seconds, *, allow_download=False, sample_seconds=SAMPLE_SECONDS):
    """Use the real MusicGen neural model, then fit/loop to the video duration.

    This intentionally distinguishes neural sample length from soundtrack
    length: MusicGen Small makes a short prompted clip and we crossfade its
    repetitions to match the exact requested length.
    """
    if not isinstance(prompt, str) or not 5 <= len(prompt.strip()) <= MAX_PROMPT:
        raise MusicGenError("Describe the music in 5–500 characters.")
    if type(seconds) is not int or not MIN_TARGET <= seconds <= MAX_SECONDS:
        raise MusicGenError("Soundtrack length must be 6–180 seconds.")
    if type(sample_seconds) is not int or not 5 <= sample_seconds <= 20:
        raise MusicGenError("Neural sample must be 5–20 seconds.")
    if not dependencies_ready():
        raise MusicGenError("Install the optional local MusicGen dependencies first.")
    try:
        import numpy as np
        import torch
        from transformers import AutoProcessor, MusicgenForConditionalGeneration
    except (ImportError, RuntimeError) as exc:
        raise MusicGenError("MusicGen's local dependencies are unavailable or incompatible.") from exc

    # Inference is local. A first-use HF model download requires UI approval.
    try:
        processor = AutoProcessor.from_pretrained(MODEL_ID,
                                                   local_files_only=not allow_download)
        model = MusicgenForConditionalGeneration.from_pretrained(
            MODEL_ID, local_files_only=not allow_download)
    except (OSError, ValueError, RuntimeError) as exc:
        if not allow_download:
            raise MusicGenError(
                "MusicGen Small is not cached. Enable the first-use model download."
            ) from exc
        raise MusicGenError("Could not load/download the free MusicGen Small model.") from exc

    device = "cpu"
    try:
        if torch.cuda.is_available():
            available, _ = torch.cuda.mem_get_info()
            total = torch.cuda.get_device_properties(0).total_memory
            # Avoid competing with CogVideoX/SD-Turbo on 6GB machines.
            # MusicGen Small can run on CPU, albeit more slowly.
            if available >= 8 * 1024 ** 3 and total >= 10 * 1024 ** 3:
                device = "cuda"
        model = model.to(device).eval()
        inputs = processor(text=[prompt.strip()], padding=True, return_tensors="pt")
        inputs = {key: value.to(device) for key, value in inputs.items()}
        with torch.inference_mode():
            audio = model.generate(
                **inputs, do_sample=True, guidance_scale=3.0,
                max_new_tokens=sample_seconds * 50)
        sample_rate = int(model.config.audio_encoder.sampling_rate)
        if not 8000 <= sample_rate <= 48000:
            raise MusicGenError("Unsupported neural audio sample rate.")
        # MusicGen emits [batch, channels, frames]. Stereo is downmixed for
        # safe memory and disk use on resource-limited Windows hardware.
        tensor = audio[0]
        if tensor.ndim == 2:
            tensor = tensor.mean(dim=0)
        raw = tensor.float().cpu().numpy()
        soundtrack = _seamless_repeat(raw, seconds * sample_rate, sample_rate, np)
        pcm = (soundtrack * 32767).astype("<i2")
        destination = Path(output)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(destination), "wb") as f:
            f.setnchannels(1)
            f.setsampwidth(2)
            f.setframerate(sample_rate)
            f.writeframes(pcm.tobytes())
        return {
            "duration": seconds, "sample_seconds": sample_seconds,
            "sample_rate": sample_rate, "device": device,
            "model": MODEL_ID, "looped": seconds > sample_seconds,
        }
    except (RuntimeError, ValueError, MemoryError, OSError) as exc:
        if "out of memory" in str(exc).lower():
            raise MusicGenError(
                "Not enough memory for MusicGen. Close GPU apps or use CPU mode."
            ) from exc
        if isinstance(exc, MusicGenError):
            raise
        raise MusicGenError("Local MusicGen inference failed. Check model dependencies and available memory.") from exc
    finally:
        del model
        if device == "cuda":
            torch.cuda.empty_cache()
