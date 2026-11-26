# AI Video Editor & Clipper

**Build videos from a topic, or turn longer recordings into short clips — locally and without a paid AI subscription.**

The app uses Python, Flask, FFmpeg and (optionally) local [Ollama](https://ollama.com) models. Creator Studio combines scripts, narration, scene footage, captions, music and transitions into an MP4. The Clipper works with your own longer videos.

> **Project status:** Active development. Windows and Linux CI run real FFmpeg integration tests; local AI inference, external stock API calls, actual narrator quality and hardware-specific GPU modes are **not yet fully validated**. Always review generated output before publishing.

## Table of contents

1. [What is included?](#what-is-included)
2. [Requirements](#requirements)
3. [Install on Windows — step by step](#install-on-windows--step-by-step)
4. [Create your first video](#create-your-first-video)
5. [Free stock API keys (optional)](#free-stock-api-keys-optional)
6. [Optional AI models and voices](#optional-ai-models-and-voices)
7. [Linux and macOS setup](#linux-and-macos-setup)
8. [Troubleshooting](#troubleshooting)
9. [Tests, privacy and limitations](#tests-privacy-and-limitations)

## Architecture

The application combines two workflows: Creator Studio for topic-to-video production and Clipper for editing existing recordings. A Flask backend coordinates project state, local AI inference, media sourcing, narration, caption generation and FFmpeg rendering. Optional AI engines are installed separately from the core editor.

## What is included?

| Feature | Description |
| --- | --- |
| AI script writer | Create scripts using local Ollama, or write your own |
| Narration | Windows SAPI, optional Edge TTS, Kokoro, Piper or your own WAV voice |
| Visuals | Upload footage, find free stock clips, or generate and animate AI images |
| Subtitles | SRT export, burnt-in animated captions, optional Whisper alignment |
| Music | Uploaded audio, local instrumental tracks and optional MusicGen |
| Video editing | Per-scene trimming, playback speed, framing, crop positions, transitions |
| MP4 export | FFmpeg rendering, file verification, preview and download |
| Automation | Topic-to-video workflow, sequential batch jobs, pause/retry/recovery |
| Clipper | Clip and subtitle existing longer recordings |

**The basic app does not require an AI GPU, cloud key or paid subscription.** Optional features can require free accounts, internet access, model downloads and substantial memory.

## Requirements

Install these first:

- **Windows 10/11 (recommended)** and **Python 3.11 (64-bit)**: https://www.python.org/downloads/ — select *Add Python to PATH* during installation.
- **Git** for downloading and updating: https://git-scm.com/downloads
- **FFmpeg and FFprobe**, including the **libx264** encoder: https://ffmpeg.org/download.html — add FFmpeg's **bin** directory to your system PATH.
- **Ollama** for AI-written scripts: https://ollama.com/download — not needed if scripts are written manually.
- Enough disk space for videos and optional model files. Models may be multiple gigabytes.

In a **new PowerShell** terminal, check:

```powershell
py -3.11 --version
git --version
ffmpeg -version
ffprobe -version
```

If the Python launcher is not installed, use `python` instead of `py -3.11` in the setup steps, provided `python --version` shows Python 3.11. Reopen PowerShell after editing PATH.

## Install on Windows — step by step

### Step 1: Download the project

```powershell
git clone https://github.com/AbdulRafay5/AI-Video-editor-and-Clipper.git
cd AI-Video-editor-and-Clipper
```

If the repository is **private**, you must have GitHub access and authenticate to clone it. You can also download the project ZIP from the GitHub repository and open PowerShell inside the extracted folder.

### Step 2: Set up Python

From the repository root:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

This creates an isolated virtual environment and installs the **core** dependencies. You don't have to install any optional AI packages to launch the editor.

### Step 3: Set up Ollama (AI script writing)

Install and run Ollama, then:

```powershell
ollama pull qwen3:4b
ollama list
```

The default app model is `qwen3:4b`, hosted by Ollama on `http://127.0.0.1:11434`. The first model download needs internet. Keep Ollama running while generating scripts.

You can instead use any **compatible model already installed** in Ollama, such as `qwen3:8b`: change **LLM_MODEL** on the app's Settings page to the *exact model name* listed by `ollama list`, then restart the app.

### Step 4: Start the app

```powershell
.\.venv\Scripts\python.exe app.py
```

Open **http://127.0.0.1:5000** in your web browser. Leave the terminal running; **Ctrl+C** stops the server.

You can check the environment using **Check setup** inside Creator Studio or directly visit:

- http://127.0.0.1:5000/api/creator/diagnostics
- http://127.0.0.1:5000/api/status

These checks reveal missing prerequisites but do **not** certify GPU model performance.

## Create your first video

For the easiest first run, **use footage you already own**; this requires no stock API keys or large image models.

1. Open **Creator Studio** and create a project with a short topic.
2. Generate a script using Ollama, or paste and save a script manually.
3. Select an available voice (Windows system voices are an offline starting point), or upload your own WAV narration segments.
4. Add footage for the **hook, every scene and closing**. MP4/MOV/MKV footage is supported by the editor.
5. Optionally configure subtitles, music, effects, trims and transitions.
6. Click **Render complete video** and let FFmpeg finish.
7. Preview and download the MP4; **watch and listen to it before publishing**.

For automated production, configure a visual mode and approve the required downloads, then start the topic-to-video workflow. Batch jobs process multiple videos sequentially. No external publishing happens automatically.

## Free stock API keys (optional)

Only needed if you want the app to **find and download stock video**. You can use your own footage without any key.

| Provider | Obtain a developer key | Environment variable |
| --- | --- | --- |
| Pexels | https://www.pexels.com/api/ | `PEXELS_API_KEY` |
| Pixabay | https://pixabay.com/api/docs/ | `PIXABAY_API_KEY` |
| Coverr | https://coverr.co/developers/ | `COVERR_API_KEY` |

Set the keys **only for providers you use**, in the same PowerShell session where you will start Flask:

```powershell
$env:PEXELS_API_KEY = "YOUR_PEXELS_KEY"
$env:PIXABAY_API_KEY = "YOUR_PIXABAY_KEY"
$env:COVERR_API_KEY = "YOUR_COVERR_KEY"
.\.venv\Scripts\python.exe app.py
```

These commands set temporary session variables; in a new terminal they must be set again. If you want persistent variables, configure them in your operating system's user environment settings. **Never commit or share real API keys.**

Provider rate limits, API behaviour and footage licensing can change. Real provider calls have **not** been verified in CI; make a small approved search/download test before depending on a provider. Check the source and license for any commercial use.

## Optional AI models and voices

**Skip this section until the basic video workflow works.** Optional features are installed separately.

| Feature | How to set it up |
| --- | --- |
| Edge TTS voices (online, no API key) | Install `requirements-voice.txt`; see [narration guide](docs/free-narration.md) |
| Kokoro local TTS | Run a separately installed compatible Kokoro server; see [narration guide](docs/free-narration.md) |
| Piper offline TTS | Install Piper, provide a local voice model and set `PIPER_MODEL` |
| AI still images + camera movement | Install `requirements-images.txt`; see [image guide](docs/free-images.md) |
| MusicGen Small soundtracks | Install `requirements-music.txt`; see [music guide](docs/free-musicgen.md) |
| True CogVideoX-2B text-to-video | Experimental: `requirements-video.txt`; see [limitations and setup](docs/free-neural-video.md) |

Install the optional **PyTorch** build compatible with your OS and CUDA requirements using https://pytorch.org/get-started/locally/ **before** installing optional image/music/video dependencies. Then, for example:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-images.txt
.\.venv\Scripts\python.exe -c "import torch; print('CUDA available:', torch.cuda.is_available())"
```

If you only want Edge TTS:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-voice.txt
```

Restart the Flask app after installing optional dependencies. Model weights download only when you explicitly permit first-use downloads.

**Important distinctions:** AI image mode produces *still images with zoom/pan*, not genuinely moving neural video. MusicGen produces a short neural sample and extends it. CogVideoX on a **6 GB RTX 3060 Laptop GPU is experimental** and may run out of VRAM or RAM; it is not a guaranteed supported configuration.

## Linux and macOS setup

The main setup instructions target Windows. Linux is covered by automated FFmpeg tests; **macOS has not received equivalent acceptance testing**. Windows SAPI voices are unavailable on Linux/macOS.

Install Python 3.11, Git, FFmpeg/FFprobe and (optionally) Ollama using platform-appropriate installers, then from a terminal:

```bash
git clone https://github.com/AbdulRafay5/AI-Video-editor-and-Clipper.git
cd AI-Video-editor-and-Clipper
python3.11 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
ollama pull qwen3:4b  # optional: only if Ollama is installed
.venv/bin/python app.py
```

Visit **http://127.0.0.1:5000**. Check any platform-specific font, FFmpeg and voice requirements if a feature fails.

## Troubleshooting

| Problem | What to try |
| --- | --- |
| Python/Git/FFmpeg command not found | Install the missing tool, add it to PATH, open a **new** terminal and check its version |
| Python `ModuleNotFoundError` | Use the virtual environment's Python, run commands from the repo root, and reinstall `requirements.txt` |
| Ollama offline/model unavailable | Start Ollama, run `ollama list`, and confirm **LLM_MODEL** matches the installed model |
| Port 5000 already taken | Stop the other local server using port 5000 |
| Narration engine missing | Choose an installed voice; Edge needs internet; Kokoro/Piper require separate installation |
| No stock results | Check API keys, provider availability/quotas, connectivity, and approval settings |
| Subtitle/FFmpeg error | Verify `ffmpeg -version`, `ffprobe -version` and a build with required subtitle/libass features |
| CUDA out-of-memory | Try a lighter visual source; optional model inference depends on available VRAM/RAM |
| App shows old interface | Restart Flask and hard-refresh the browser |
| Interrupted/blocked job | Inspect the job error, fix its prerequisite, and use retry instead of deleting saved work |

Application logs are at **`data/clipper.log`**, with generated projects under **`data/creator/`**. Never post secrets or private content when sharing logs.

## Tests, privacy and limitations

You can run local checks on Windows:

```powershell
.\.venv\Scripts\python.exe scripts/smoke_startup.py
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -v
```

If Node.js is installed, frontend polling tests are available:

```powershell
node tests/creator_frontend_polling.test.cjs
```

The startup smoke test starts the full application and requests its main endpoints. The regression tests include real FFmpeg media generation and mocked optional AI/external services. GitHub Actions runs these tests on **Windows and Ubuntu**. **Passing CI does not mean a downloaded model or live stock provider has been verified on your hardware.**

### Local acceptance checklist

- [ ] Create a Creator Studio project and generate/save a valid script
- [ ] Generate usable narration (or upload WAV) and check speech timing
- [ ] Upload visual footage for every scene
- [ ] Render and play a final MP4 with audio, captions and transitions
- [ ] Edit a scene and confirm the next render reflects it
- [ ] If configured, test stock sourcing, optional AI images and music
- [ ] Pause/retry a batch and check completed output is retained

**Privacy and safety:** The app binds to `127.0.0.1` by default. Local projects, uploads, exports and logs are stored in `data/` (excluded from Git). **Back up final MP4s before deleting a Creator project** because that also deletes its associated media. Edge TTS, stock providers and first-use model downloads involve external services. Review video, narration, facts, media permissions, third-party licenses and final export quality before sharing.

**Not implemented/fully verified:** social-platform auto-publishing, fully validated true AI text-to-video inference on the target GPU, a full draggable frame-accurate timeline and computer-vision-based automatic B-roll matching.

## Further guides

- [Narration / Kokoro / Edge / Piper](docs/free-narration.md)
- [Local AI images](docs/free-images.md)
- [Local MusicGen](docs/free-musicgen.md)
- [Experimental CogVideoX](docs/free-neural-video.md)
## Third-party notices

The revision renderer includes MIT-licensed components from [MoneyPrinterTurbo](https://github.com/harry0703/MoneyPrinterTurbo). See the [bundled copyright and license notice](clipper/mpt_compat/LICENSE) and [component provenance notes](docs/mpt-free-port.md). Third-party AI models, voices, stock footage and services have separate licenses and usage terms.
