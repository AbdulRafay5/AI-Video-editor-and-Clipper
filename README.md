# AI Video Editor and Clipper

Local-first video editing and automatic Creator Studio production. Uses **FFmpeg,
Python, local Ollama** and optional free TTS, stock providers and local image
models. No paid AI subscription or video-publishing API is required.

> **Status:** Under active development. GitHub Actions exercises the offline
> Python/FFmpeg regression suite and frontend polling tests, but **does not
> certify model inference on Windows, CUDA compatibility, or real stock API
> downloads**. Review generated footage and captions before publishing.

## Windows quick start

Requirements: **Windows 10/11**, **Python 3.11**, **FFmpeg + FFprobe on PATH**.
For AI script generation, install [Ollama](https://ollama.com/) and download a
local model; the default configuration uses `qwen3:4b`.

In PowerShell, at the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
ffmpeg -version
ffprobe -version
ollama pull qwen3:4b
.\.venv\Scripts\python.exe app.py
```

Open **http://127.0.0.1:5000**. Keep Ollama running locally when using AI
script writing. To use a different installed model (for example `qwen3:8b`),
change **LLM_MODEL** in the Settings page and restart the app. No model is
silently downloaded by Creator Studio.

The runtime creates its database, generated media and application logs under
`data/` (excluded from Git). Important: deleting a Creator project also
deletes its local project media. Export any MP4 you need to retain first.

## Creator Studio workflow

1. Create a project from a topic or save a manually edited script.
2. Generate narration (choose an available voice) or import recorded WAV parts.
3. Supply footage for the hook, every scene and closing, or choose free stock
   downloads or local AI-generated still images with camera movement.
4. Optionally choose background music, subtitle alignment, transitions and
   editing preferences.
5. Render and preview the final MP4, then review it before publishing.

Batch automation creates independent projects and runs them sequentially. A
paused batch may finish its already-running video before stopping. Completed
stages can be reused on retry; expensive local AI inference is serialized
across script writing, AI image generation, MusicGen and neural video.

## Optional dependencies

The core application does not require all the experimental AI packages.
Install optional requirements **only** for the feature you intend to use:

| Feature | Optional setup |
| --- | --- |
| Local SD-Turbo still images | `requirements-images.txt` + compatible local PyTorch |
| Neural soundtrack (MusicGen Small) | `requirements-music.txt` + compatible local PyTorch |
| Additional local/neural voices | `requirements-voice.txt`; check [voice instructions](docs/free-narration.md) |
| Experimental true neural video | `requirements-video.txt` + compatible CUDA PyTorch; see [limitations](docs/free-neural-video.md) |

The RTX 3060 Laptop GPU (6 GB) is **not a confirmed target for true local
text-to-video inference**. Treat that mode as experimental/deferred. AI image
animation and stock footage are the practical visual paths.

Optional stock footage requires provider-issued **free API keys** such as
`PIXABAY_API_KEY` or `PEXELS_API_KEY`, supplied in the local process
environment. No key is required to upload your own footage.

See [MoneyPrinterTurbo port and limits](docs/mpt-free-port.md),
[image setup](docs/free-images.md), and
[MusicGen setup](docs/free-musicgen.md).

## Verification

GitHub Actions (`.github/workflows/creator-tests.yml`) runs syntax checks,
a Node.js frontend polling regression, and the Python regression suite with
FFmpeg on Ubuntu. It intentionally uses offline doubles for expensive AI
model inference and external services.

To run Python tests manually in the virtual environment:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -v
```

With Node.js available, frontend polling tests can also be run using
`node tests/creator_frontend_polling.test.cjs`.

### Final Windows acceptance checklist

- Verify app startup and Creator Studio project creation.
- Verify Ollama script generation with the configured **local** model.
- Verify your chosen narration voice and word/subtitle timing.
- Render one project from **actual local footage**, preview it, and play the
  exported MP4 with audio.
- Verify the free stock/AI-image fallback *only if configured*.
- Edit a scene or music choice and confirm the previous export is invalidated,
  then re-render.
- Test a batch, pause/restart it, and check that completed output is retained.
- Confirm files under `data/` and check `data/clipper.log` after an error.

**Not included in the current acceptance scope:** unattended publishing to
Instagram/YouTube/TikTok and fully verified true local neural video generation.
