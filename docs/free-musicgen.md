# Free local neural music in Creator Studio

Creator Studio can now generate **real text-conditioned neural background
music** with Meta's **MusicGen Small**, a 300M-parameter local model.

This is **not** the same as MoneyPrinterTurbo's Sonilo/ElevenLabs video-to-music
cloud API integrations. Those depend on third-party accounts and may charge.
This alternative generates audio from a text description **on your own
computer**, with no inference/API fees. It retains the old instrumentals,
uploads, presets and random-track options.

## Windows installation

1. Open your existing Creator Studio project virtual environment in a terminal.
2. Install compatible PyTorch for your Windows Python and NVIDIA GPU from
   https://pytorch.org/get-started/locally/ . CPU-only PyTorch is also
   supported. You may use `pip install torch` for CPU operation.
3. Run `python -m pip install -r requirements-music.txt`.
4. Ensure FFmpeg/FFprobe are installed and on PATH, then restart the Flask app.
5. In Creator Studio, open a project and find **Neural AI soundtrack · local
   MusicGen Small**. Select a mood, type a prompt (or leave blank to derive
   one from your video topic/script) and choose **Match narration**.
6. For the first run, tick **Allow free first-use MusicGen model download**.
   The model weights are a sizable download from Hugging Face. Later runs
   can leave this unchecked and use locally cached weights.
7. Click **Generate neural soundtrack locally**. The project reports status.
   When successful, the WAV becomes the active background music and will be
   mixed by the existing MoneyPrinterTurbo-derived project renderer.

The local inference may be **slow**. MusicGen Small is 300M parameters, and
memory usage varies by hardware and available VRAM. On machines with less
than roughly 4GiB *free* VRAM, the adapter uses CPU. A computer with an
RTX 3060 6GB can sometimes use its GPU, but live performance has **not** been
benchmarked on that specific machine.

## How duration and video matching work

MusicGen Small is prompted with the selected **mood**, the video's **topic**
and up to three **scene visual descriptions**, or your supplied custom text.

- **Match narration** uses measured narration duration where available,
  otherwise the project's target duration.
- Lengths are bounded to **6–180 seconds**. Longer projects require an
  updated generation and composition approach.
- MusicGen produces an **8–20-second neural sample**. This sample is crossfaded
  into repeated segments with intro/outro fades so that the track lasts
  exactly the requested duration. The result has real neural timbres, but
  **it is not a unique 60-second model composition**.
- The generator does not analyse the final rendered video pixels or audio;
  its mood match is based on topic and script descriptions. This is not
  identical to video-conditioned Sonilo/ElevenLabs music.
- The existing renderer is responsible for looping/mixing its background
  track and retains the narration at its original speed.

## Reliability and privacy

- Dependencies load only when the neural feature is selected. The base
  application and existing clipper do not require MusicGen or PyTorch.
- A first-use model download requires a separate explicit checkbox.
  No prompt, video or generated audio goes to paid inference APIs.
- Soundtracks are written to a project-local WAV. A new soundtrack replaces
  the active selection **only after successful generation and FFprobe
  validation**. A failure leaves the old track and previous renders intact.
- Background music can be previewed from Creator Studio after generation.
- Jobs are serialized, prevent competing Creator Studio stages, expose
  success/failure status and recover gracefully if the app restarts.
- GPU/CPU model loading and audio quality still require local Windows QA;
  GitHub CI tests the workflow with a fake neural model, not the downloaded
  MusicGen weights.

Model: https://huggingface.co/facebook/musicgen-small
Official inference examples: https://huggingface.co/docs/transformers/model_doc/musicgen
Model licensing and redistribution terms remain distinct from this app.
