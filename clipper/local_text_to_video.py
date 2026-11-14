"""Local *neural* text-to-video generation with CogVideoX-2B.

Unlike image animation this model synthesizes an independent sequence of frames.
No cloud inference, paid APIs, or silent model downloads. Optional dependencies
are imported only when generating. Using sequential CPU offloading can be very
slow and requires significant system RAM despite low GPU memory usage.
"""
import gc
import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

MODEL = "zai-org/CogVideoX-2b"
MAX_PROMPT = 750
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class VideoGenerationError(ValueError):
    pass


def dependencies_ready():
    return all(importlib.util.find_spec(name) is not None
               for name in ("torch", "diffusers", "transformers", "accelerate", "PIL", "imageio"))


def prompt_for(slot, script):
    scenes = script.get("scenes") or []
    if not scenes:
        raise VideoGenerationError("The saved script has no scenes.")
    if slot == "hook":
        idea = scenes[0].get("visual_idea", "")
    elif slot == "closing":
        idea = scenes[-1].get("visual_idea", "")
    elif slot.startswith("scene_") and slot[6:].isdigit():
        index = int(slot[6:])
        if index >= len(scenes):
            raise VideoGenerationError("Invalid scene.")
        idea = scenes[index].get("visual_idea", "")
    else:
        raise VideoGenerationError("Invalid scene.")
    idea = str(idea).strip()
    if not 2 <= len(idea) <= 550:
        raise VideoGenerationError("A useful English visual idea is required.")
    return ("Continuous cinematic motion shot, realistic moving subjects and "
            "natural camera movement, consistent anatomy and lighting, no "
            "subtitles, no logos. " + idea)[:MAX_PROMPT]


def probe_video(path):
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise VideoGenerationError("FFprobe must be available on PATH.")
    try:
        done = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height,nb_frames:format=duration",
             "-of", "json", str(path)],
            capture_output=True, text=True, timeout=30, creationflags=NO_WINDOW)
        if done.returncode:
            raise VideoGenerationError("FFprobe could not validate the generated video.")
        info = json.loads(done.stdout)
        stream = next(iter(info.get("streams") or []), {})
        seconds = float((info.get("format") or {}).get("duration") or 0)
        width, height = int(stream.get("width") or 0), int(stream.get("height") or 0)
        if not (0.5 < seconds <= 20 and width >= 256 and height >= 256):
            raise VideoGenerationError("The neural model produced an invalid video.")
        return {"width": width, "height": height, "seconds": round(seconds, 2)}
    except (OSError, ValueError, TimeoutError, subprocess.TimeoutExpired) as exc:
        if isinstance(exc, VideoGenerationError):
            raise
        raise VideoGenerationError("Cannot validate the generated video.") from exc


class LocalCogVideoX:
    """Load one pipeline for a whole project, releasing memory afterward."""

    def __init__(self, *, allow_download=False):
        self.allow_download = allow_download
        self.pipe = None
        self.torch = None

    def __enter__(self):
        if not dependencies_ready():
            raise VideoGenerationError(
                "Install requirements-video.txt and a CUDA-enabled PyTorch build.")
        import torch
        from diffusers import CogVideoXPipeline
        if not torch.cuda.is_available():
            raise VideoGenerationError(
                "CogVideoX requires NVIDIA CUDA for this Windows integration. "
                "Check your PyTorch CUDA installation.")
        self.torch = torch
        # The 2B checkpoint is FP16 and can use disk-cache-only inference.
        # Download permission never changes because a retry occurs.
        try:
            self.pipe = CogVideoXPipeline.from_pretrained(
                MODEL, torch_dtype=torch.float16,
                local_files_only=not self.allow_download)
            self.pipe.enable_sequential_cpu_offload()
            self.pipe.vae.enable_slicing()
            self.pipe.vae.enable_tiling()
        except (OSError, ValueError, RuntimeError, ImportError) as exc:
            self.__exit__(None, None, None)
            msg = ("Could not load CogVideoX-2B. Check model access, free RAM, "
                   "disk space and dependency compatibility." if self.allow_download
                   else "CogVideoX-2B is not cached or could not load. Approve "
                        "the first model download or check installation.")
            raise VideoGenerationError(msg) from exc
        return self

    def generate(self, prompt, target, *, steps=30):
        if not isinstance(prompt, str) or not 5 <= len(prompt) <= MAX_PROMPT:
            raise VideoGenerationError("Video prompt must be 5–750 characters.")
        if type(steps) is not int or not 20 <= steps <= 50:
            raise VideoGenerationError("Denoising steps must be 20–50.")
        if self.pipe is None:
            raise VideoGenerationError("The local video model is not loaded.")
        from diffusers.utils import export_to_video
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self.torch.inference_mode():
                # CogVideoX-2B generates 49 separate frames at 8fps, ~6 sec.
                frames = self.pipe(
                    prompt=prompt, num_videos_per_prompt=1,
                    num_inference_steps=steps, num_frames=49,
                    guidance_scale=6.0).frames[0]
            export_to_video(frames, str(target), fps=8)
            del frames
            if not target.is_file() or target.stat().st_size < 1024:
                raise VideoGenerationError("Text-to-video produced no usable MP4.")
            return probe_video(target)
        except Exception as exc:
            target.unlink(missing_ok=True)
            if isinstance(exc, VideoGenerationError):
                raise
            raise VideoGenerationError(
                "Neural video generation failed. This may be GPU/RAM exhaustion; "
                "see the local application log.") from exc

    def __exit__(self, typ, value, tb):
        self.pipe = None
        gc.collect()
        if self.torch is not None and self.torch.cuda.is_available():
            self.torch.cuda.empty_cache()
        return False
