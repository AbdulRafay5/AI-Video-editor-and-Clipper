# Optional local AI image scenes for Creator Studio

Creator Studio now supports **free local text-to-image generation** using
`stabilityai/sd-turbo` and **cinematic still-image animation** using FFmpeg.
This is an optional alternative to Pexels/Pixabay/Coverr footage when a
scene cannot be found or when you want original imagery.

This does **not** generate neural *moving-video frames* as a text-to-video
model would. The still image is genuinely AI generated; the motion is a
smooth camera-style zoom/pan of the image.

## Windows installation

1. Update your project from the repository's `main` branch.
2. Activate your existing Python 3.11 `.venv`.
3. Install Windows PyTorch with a compatible CUDA build from
   https://pytorch.org/get-started/locally/ if you want to try GPU
   generation. CPU operation is also possible.
4. Run `python -m pip install -r requirements-images.txt`.
5. Confirm `ffmpeg -version` works, then restart the Flask application:
   `python app.py`.
6. Open a Creator Studio project with a generated script. In
   **Generate AI image scenes locally**, tick **Allow first-time download**
   on the first use. The initial model download can be several gigabytes;
   it uses the Hugging Face model hub. Leave the checkbox off after it has
   been cached locally.
7. Click **Generate missing scenes as AI images**. The app processes missing
   scenes, creates a `512x512` SD-Turbo image per missing scene, animates
   it with FFmpeg to a portrait MP4 and registers the MP4 as regular footage.
8. For automatic production, tick **Generate AI image scenes for missing
   footage**. If you also approve stock downloads, stock is tried first.
   Generated images are used only for remaining missing scenes.
   Stock downloads are not required when image fallback is enabled.

## Automatic production: two visual-source options

Creator Studio's **Automatic topic-to-video production** now has a
**Visual source** selector with two exclusive choices:

- **Stock footage — Pexels / Pixabay / Coverr:** approve stock downloads,
  configure a free provider API key, and reuse or download stock clips.
  No local image model is called.
- **AI-generated visuals — local SD-Turbo + cinematic motion:** do not
  call any stock provider. Generate a **new local AI image for every script
  slot**, including the opening and ending, then turn each into an animated
  MP4 for the normal renderer. Explicit first-use model-download permission
  is still required.

AI-only mode regenerates all scene visuals even if the project already has
stock or manually uploaded clips. To protect existing work, the new scene
files are prepared separately, then their **scene references are swapped
together only after all images and animations succeed**. A failed run leaves
existing footage selections unchanged, and original media files are not
deleted. Existing projects can be used with either mode.

**Important:** AI-generated visuals currently means real text-to-image
generation plus camera-motion animation. It is *not* neural text-to-video
synthesis of new moving subjects. True model-generated video clips remain a
separate future provider/model integration.

## Hardware and behavior

- This is intended as a cautious starting point for an RTX 3060 with
  **6 GB VRAM**, rather than a full-resolution SDXL setup.
- Images are currently generated at **512x512**; the animation stage crops
  and enlarges the image to **720x1280**. Portrait sharpness is limited by
  the original image resolution.
- The Diffusers pipeline uses CPU offload on CUDA systems to reduce VRAM
  pressure. It may run slowly and need extra RAM. Real model inference on
  your Windows hardware is **not yet verified**.
- All inference is on your computer after the model is downloaded.
  There is no paid inference API, no automatic license acceptance, and no
  cloud video generation.
- Existing manual or stock footage is never replaced. Successful image
  scenes are stored in `data/creator/<id>/visuals/` as PNG + MP4; the
  original image and prompt are recorded alongside the scene reference.
- If a model is missing and downloading is not approved, the job stops
  with a clear message. On failure, previously saved scenes remain usable.
- The standard MoneyPrinterTurbo-derived FFmpeg renderer, existing long-video
  clipper and cached projects are not modified.

Model: https://huggingface.co/stabilityai/sd-turbo

The model is subject to its own license and usage conditions; review them
before distributing or monetizing model-generated content.

## Verification

GitHub Actions runs syntax checks and unit tests for download consent,
safe scene replacement, interrupted-job recovery and a **real FFmpeg
still-image-to-MP4 render**. Neural SD-Turbo weights are not downloaded in
CI. A real on-device run must be checked for reliability and output quality.
