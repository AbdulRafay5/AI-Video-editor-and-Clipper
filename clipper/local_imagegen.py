"""Optional local image generation and cinematic still-to-MP4 conversion.

Diffusers is deliberately imported only upon use. First model download must
be explicitly approved. This model is independent of MoneyPrinterTurbo's
cloud image/video APIs. Users are responsible for model license compliance.
"""
import importlib.util
import shutil
import subprocess
from pathlib import Path

MODEL = "stabilityai/sd-turbo"
MAX_PROMPT = 400
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class ImageGenerationError(ValueError):
    pass


def dependencies_ready():
    return all(importlib.util.find_spec(name) is not None
               for name in ("torch", "diffusers", "transformers", "accelerate", "PIL"))


def image_prompt(slot, script):
    if slot == "hook":
        scene = script["scenes"][0]
    elif slot == "closing":
        scene = script["scenes"][-1]
    elif slot.startswith("scene_"):
        scene = script["scenes"][int(slot.split("_")[1])]
    else:
        raise ImageGenerationError("Unknown scene reference.")
    idea = str(scene["visual_idea"]).strip()
    if not 2 <= len(idea) <= 350:
        raise ImageGenerationError("Scene visual description is invalid.")
    return ("Cinematic photograph, detailed realistic scene, consistent natural "
            "lighting, no captions or logos. " + idea)[:MAX_PROMPT]


def generate_image(prompt, target, *, allow_download=False):
    """Use a locally executed image model; no paid endpoint or silent download."""
    if not isinstance(prompt, str) or not 5 <= len(prompt) <= MAX_PROMPT:
        raise ImageGenerationError("Image prompt must be 5–400 characters.")
    if not dependencies_ready():
        raise ImageGenerationError(
            "Install optional image dependencies from requirements-images.txt.")
    import torch
    from diffusers import AutoPipelineForText2Image

    try:
        # SD-Turbo is practical at 512x512; CPU offload is preferred on
        # memory-limited NVIDIA laptops. Aspect ratio is handled by FFmpeg.
        pipe = AutoPipelineForText2Image.from_pretrained(
            MODEL, torch_dtype=torch.float16 if torch.cuda.is_available() else torch.float32,
            local_files_only=not allow_download)
    except (OSError, ValueError, RuntimeError) as exc:
        if not allow_download:
            raise ImageGenerationError(
                "Image model is not cached. Approve the initial model download."
            ) from exc
        raise ImageGenerationError("Could not load/download local SD-Turbo model.") from exc

    try:
        if torch.cuda.is_available():
            pipe.enable_model_cpu_offload()
        else:
            pipe = pipe.to("cpu")
        with torch.inference_mode():
            photo = pipe(prompt=prompt, num_inference_steps=2,
                         guidance_scale=0.0, height=512, width=512).images[0]
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        photo.save(target, format="PNG")
        if not target.is_file() or target.stat().st_size < 512:
            raise ImageGenerationError("Local model did not create a usable image.")
        return {"model": MODEL, "width": 512, "height": 512}
    except (RuntimeError, OSError, ValueError) as exc:
        if isinstance(exc, ImageGenerationError):
            raise
        raise ImageGenerationError(
            "Local image generation failed. Check RAM/VRAM or model setup."
        ) from exc
    finally:
        del pipe
        if torch.cuda.is_available():
            torch.cuda.empty_cache()


def animate_still(source, target, *, seconds=5, width=720, height=1280, fps=24):
    """Produce loopable zoom-pan footage suitable for the existing video renderer."""
    if not isinstance(seconds, (int, float)) or not 1 <= seconds <= 40:
        raise ImageGenerationError("Scene animation duration must be 1–40 seconds.")
    if (type(width) is not int or type(height) is not int or width % 2
            or height % 2 or not 256 <= width <= 1920 or not 256 <= height <= 1920):
        raise ImageGenerationError("Invalid animation dimensions.")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise ImageGenerationError("FFmpeg is required for cinematic image animation.")
    source, target = Path(source), Path(target)
    if not source.is_file() or source.suffix.lower() != ".png":
        raise ImageGenerationError("A locally generated PNG is required.")
    target.parent.mkdir(parents=True, exist_ok=True)
    frames = round(seconds * fps)
    # Generate slightly oversized source, then gradually zoom in and pan.
    filtergraph = (
        f"scale={width*2}:{height*2}:force_original_aspect_ratio=increase,"
        f"crop={width*2}:{height*2},"
        f"zoompan=z='min(zoom+0.0009,1.12)':"
        f"x='(iw-iw/zoom)*0.45':y='(ih-ih/zoom)*0.50':"
        f"d={frames}:s={width}x{height}:fps={fps},"
        "setsar=1,format=yuv420p"
    )
    args = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-loop", "1", "-i", str(source), "-vf", filtergraph,
            "-frames:v", str(frames), "-an", "-c:v", "libx264",
            "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", str(target)]
    try:
        done = subprocess.run(args, capture_output=True, text=True,
                              timeout=240, creationflags=NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired) as exc:
        target.unlink(missing_ok=True)
        raise ImageGenerationError("Image animation could not run.") from exc
    if done.returncode or not target.is_file() or target.stat().st_size < 1024:
        target.unlink(missing_ok=True)
        raise ImageGenerationError("FFmpeg image animation failed: " + done.stderr[-240:])
    return target
