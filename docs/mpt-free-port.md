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

## Automatic free stock sourcing (Creator Studio)

Creator Studio now offers **Find footage for missing scenes** using the existing
free Pixabay API integration. Set `PIXABAY_API_KEY` using a free Pixabay
developer account. This does not require a subscription or a paid API.

On each explicit click it searches up to six currently empty scenes using
their saved visual descriptions. Matching follows MoneyPrinterTurbo's
duration/orientation/resolution principles and prioritises manageable,
portrait-format MP4s. It uses a bounded download, verifies the MP4 through
FFprobe and records the Pixabay source link. Existing manually uploaded footage
is never overwritten. Each batch reports misses and preserves previously saved
scenes. Subsequent clicks continue with the remaining scenes.

**Important limitation:** Keyword/metadata matching is not the same as visually
examining the footage. Always review the original source and output for
relevance, safety and usage rights. Source URLs are exposed in the Creator
Studio UI. Pixabay content is subject to its current licence and any
restrictions affecting recognisable persons, logos or trademarks.

The automatic stock search is a **new compatibility adapter** based on the
material-selection practices of MoneyPrinterTurbo's `app/services/material.py`.
It is not a verbatim copy of that provider module, because the original has
dependencies on MoneyPrinterTurbo-specific schemas, provider settings,
loggers and multi-provider request code. The existing clipper search is reused
without changing its routes or API responses. The 946-line revision renderer
remains the direct upstream code copy.

## Creator Studio captions and finishing

The immutable upstream MoneyPrinterTurbo revision renderer is still responsible
for scene composition, voice, background music and its verified render cache.
`clipper/creator_finish_api.py` applies **opt-in-derived** FFmpeg finishing
to a separate file under `data/creator/<id>/finished/`. The original cached
render and the clipper's existing video output are unchanged.

The implementation **reuses the original clipper's ASS subtitle builder**,
rather than copying MoneyPrinterTurbo's entire MoviePy/TextClip subtitle
pipeline. A Creator Studio script/narration can export SRT captions or burn
them into the final MP4. Captions use approximate word timing distributed
within measured TTS segment boundaries; Kokoro and similar speech backends
do not supply precise word-level timing. No karaoke synchronization is
claimed. Exact word sync would require a follow-up local Whisper alignment
step. Finishing options also provide short **start/end fades**; this does
**not** implement crossfades or animation between scenes.

Options may be switched off in Creator Studio. Users can download the
original unmodified upstream render when both are off. This stage requires
an FFmpeg build with libass support when burning captions.

## Free feature parity expansion (November 2026)

### Three stock-footage providers

`clipper/creator_providers.py` adapts MoneyPrinterTurbo's
`material.py` selection strategy for **Pixabay, Pexels and Coverr**.
Creator Studio searches whichever free developer credentials you configure:

- `PIXABAY_API_KEY`
- `PEXELS_API_KEY`
- `COVERR_API_KEY`

The user must explicitly approve a batch before downloads begin. Provider
host allowlists, bounded file sizes, local media validation, source metadata
and manual review controls are enforced. Accounts, request quotas and actual
availability depend on provider terms; no paid tiers are required by our code.
Live provider requests were not executed in CI.

### Measured subtitles

`clipper/creator_alignment_api.py` uses the same free
`faster-whisper` word-timestamp approach as MoneyPrinterTurbo. User-triggered
local CPU/int8 transcription uses `tiny.en`; its initial public-model download
needs Internet, and accuracy depends on the recording. When using Edge TTS,
the narration system also records actual Edge WordBoundary events and
automatically favours them over estimated segment timings. Kokoro/Piper/Windows
voices can use Whisper alignment. Existing saved approximate timing remains
the fallback, not the preferred source of truth.

### Scene transitions

An optional post-processing stage supports real FFmpeg xfade **crossfade,
slide-left, slide-right and zoom** effects between scenes, using immutable
scene video artifacts from the copied MoneyPrinterTurbo renderer. Brief
start/end fades remain separately configurable. The transition logic pads
outgoing scene visuals so the narration audio timeline is preserved; video
stays non-destructive and exports a new MP4. Scene transition tests use real
two-scene FFmpeg rendering.

### Music

Creator Studio now has an upload/reusable-preset/random-track library plus
four original **locally synthesized instrumental moods**. These soundtracks
are generated locally, without a sample pack or paid service. This is not
equivalent to a neural, prompt-to-music model. MoneyPrinterTurbo's cloud
Sonilo/ElevenLabs music generation involves separate provider terms and
potential charges; those endpoints are **not** ported in a free-only build.
Upstream preset MP3s are not copied because their audio redistribution rights
were not independently verified, even though the code is MIT-licensed.

## Still missing compared with MoneyPrinterTurbo

- Visual analysis of downloaded footage and more advanced semantic per-scene selection
- Local image synthesis and practical model-tested text-to-video generation
- Alignment quality checks, richer subtitle animation and additional scene effects
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
