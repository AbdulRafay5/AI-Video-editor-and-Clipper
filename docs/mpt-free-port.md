# MoneyPrinterTurbo compatibility port (free-only)

## Provenance and license

- Upstream: https://github.com/harry0703/MoneyPrinterTurbo
- Directly copied module: `app/services/video_project.py`
- Location here: `clipper/mpt_compat/video_project.py`
- License: MIT; original copyright (c) 2024 Harry
- Full original license: `clipper/mpt_compat/LICENSE`

The source file is vendored unchanged except for a provenance header. The
adapter `clipper/creator_render_api.py` and the UI in `static/ext_creator.js`
are project-specific integration code, **not** copied MoneyPrinterTurbo code.

The core revision engine works entirely with local prepared footage/audio
and FFmpeg. No cloud-provider SDKs, API keys, paid generation or publishing
are involved. This is intentionally **not** an unverified wholesale copy of
MoneyPrinterTurbo: many of its optional providers require separate payments,
credentials, model servers or software dependencies.

## What the local revision renderer does

- Snapshots prepared video, narration, and optional background-music files
- Produces one scene MP4 for each narrated section
- Joins scenes into a portrait 720x1280 MP4
- Mixes uploaded background music at a conservative volume
- Fingerprints media and settings, so unchanged stages may be reused
- Saves immutable revisions; old snapshots remain available for future reuse
- Tracks completed stages, verifies artifact hashes and supports cancellation
- Uses local FFmpeg/FFprobe only

Creator Studio routes are under `/api/creator/projects/<id>/...`. Existing
clipper projects, clip exports, stock search and campaign routes are unchanged.

## How to use

1. Install FFmpeg and FFprobe into PATH.
2. Create a Creator Studio project on the home page.
3. Generate or save the script with local Ollama.
4. Generate narration using Kokoro, Edge TTS, Piper or Windows voice.
5. Upload local MP4/MOV/MKV footage for the hook, each scene and the closing.
6. Optionally upload a local MP3/WAV/FLAC/AAC music track that you can legally use.
7. Choose **Render complete video**. The creator stage status updates while it runs.
8. Download the completed MP4 or cancel an active render.

## Still missing compared with MoneyPrinterTurbo

- Fully automatic footage selection/placement, free provider integrations
  beyond the separate existing Pixabay search, and visual-content checks
- Local image synthesis and practical model-tested text-to-video generation
- Creator Studio subtitle compositing and advanced transitions
- Automated batch rendering / topic queues and social publishing
- A fully unified editor timeline with reversible B-roll and captions
- Image-only animated scenes and automatic soundtrack recommendations

These gaps should be ported or adapted independently behind opt-in paths
and tested before altering the existing clipper. None of the above missing
features should be called implemented until their tests and user flows work.

## Verification

`.github/workflows/creator-tests.yml` runs Python syntax checks and all
repository tests, including a real FFmpeg rendering smoke test when FFmpeg is
installed. The test must not be skipped when reporting a verified render.

Windows end-to-end verification on the owner's RTX 3060 system remains
necessary before calling the complete Creator Studio production-ready.
