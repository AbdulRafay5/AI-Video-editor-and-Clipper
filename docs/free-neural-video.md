# Local neural text-to-video (experimental)

Creator Studio now exposes three visual sources:
**Stock footage**, **AI images + camera motion**, and **True AI video (CogVideoX-2B)**.

The true-video option uses real diffusion-generated video frames, one short
clip for the opening, every scene, and the ending. It does **not** use Pexels,
Pixabay, Coverr, cloud video inference, or animated still-image footage.

The scene pipeline is modelled after MoneyPrinterTurbo's concept of generating
video materials for each scene, then handing ordinary MP4 clips to the renderer.
Our implementation differs from the upstream cloud APIs because it runs a
local model and does **not** submit billable tasks.

## Windows setup

1. Start with the latest code from `main` and an activated Python 3.11
   virtual environment. Leave your `data` folder intact during updates.
2. Install an appropriate Windows **CUDA-enabled** version of PyTorch from
   https://pytorch.org/get-started/locally/ (select Windows, Pip, CUDA).
3. Install optional packages:
   ```powershell
   python -m pip install -r requirements-video.txt
   python -c "import torch; print('CUDA available:', torch.cuda.is_available())"
   ```
   The second command must print `True`. A CPU-only PyTorch installation
   will be rejected with a clear error.
4. Confirm `ffmpeg -version` and `ffprobe -version` work.
5. Restart Flask: `python app.py` and open http://127.0.0.1:5000.
6. Open Creator Studio, create a project and set **Visual source** to
   **True AI video · local CogVideoX-2B (experimental)**.
7. For the first run, explicitly tick **Allow first-use CogVideoX-2B model
   download**. The model is many gigabytes and is retrieved from the Hugging
   Face model hub. Once cached, leave the checkbox unticked.
8. Start with a *short video with very few scenes*. Generation can take
   a very long time on an RTX 3060 6GB, and CUDA out-of-memory or insufficient
   **system RAM** is possible. Progress is per completed scene. Once all
   clips succeed, the existing FFmpeg renderer assembles narration, subtitles
   and music into the final MP4.

## Model and technical details

- Model: https://huggingface.co/zai-org/CogVideoX-2b
- Diffusers pipeline: `CogVideoXPipeline`
- Produces **49 neural video frames per scene at 8fps** (~6 seconds),
  independent of the image-motion mode. The existing video renderer loops
  or trims scene materials to match speech length.
- Model weights are FP16; `enable_sequential_cpu_offload()`, VAE slicing and
  VAE tiling are enabled to reduce peak VRAM. CPU offloading may use large
  amounts of system RAM and drastically slow inference.
- A single model load is reused across the scenes. Ollama is unloaded before
  CogVideoX to free GPU memory. No silent paid APIs or remote inference.
- Neural video generation uses **the currently saved script visual ideas**.
  Generating a new script can change the prompts.
- Generated clips are validated with FFprobe. The new scene references replace
  old stock or manual selections **only after all clips succeed**. Failed or
  interrupted jobs keep the previously selected visuals.
- A fully generated project is NOT automatically published. Manual review is
  still required.

## What is verified vs unverified

CI checks request validation, download consent, job recovery, scene replacement,
and the final FFmpeg video render with a stand-in model returning real video
files. **CI does not download CogVideoX weights or run GPU inference.**

Although documented low-memory CogVideoX paths have been published, this exact
pipeline has not been benchmarked on the user's Windows laptop, and success
on a 6GB GPU is **not guaranteed**. If inference fails, the project will
preserve its existing footage and show a useful error.

This is an optional experimental mode, not a claim of identical performance to
MoneyPrinterTurbo's paid cloud text-to-video services.
