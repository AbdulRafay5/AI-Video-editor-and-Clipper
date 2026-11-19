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

Creator Studio has an upload/reusable-preset/random-track library, four
algorithmically synthesized instrumental moods, and optional MusicGen Small neural soundtracks. The algorithmic soundtracks and optional neural soundtracks run locally,
without requiring a paid provider. MoneyPrinterTurbo's cloud
Sonilo/ElevenLabs music generation involves separate provider terms and
potential charges; those endpoints are **not** ported in a free-only build.
Upstream preset MP3s are not copied because their audio redistribution rights
were not independently verified, even though the code is MIT-licensed.

### Local neural soundtrack generation

MoneyPrinterTurbo's **video-to-music** API providers (Sonilo and ElevenLabs)
are distinct from Creator Studio's **local text-to-music** implementation.
`clipper/local_musicgen.py` integrates real, prompt-conditioned
`facebook/musicgen-small` neural inference using Hugging Face Transformers
and PyTorch; optional dependencies are in `requirements-music.txt`.
The `creator_neural_music_api.py` bridge provides model capabilities,
video-aware prompt preview, music-job status, generated-audio playback and
a background generation endpoint.

- A blank prompt derives a style from selected mood, the project topic and
  scene descriptions; custom text can override this.
- The target length matches measured narration duration where available,
  otherwise the video's planned duration, bounded to 6–180 seconds.
- MusicGen produces an **8–20-second neural sample**, then the app crossfades
  repetitions into a longer soundtrack and fades the endpoints. This is not
  a unique 180-second neural composition.
- Downloads occur only when a user explicitly permits an initial Hugging
  Face model download. No cloud music inference API or payments are involved.
- On failure, the previously selected music and render outputs remain intact.
  Successful output replaces the selected background track; the existing
  MoneyPrinterTurbo project renderer mixes it with narration.
- CI uses a fake neural model to test the interface and WAV generation.
  Model weights are **not downloaded or benchmarked** on Windows in CI.
- The generator uses a text description based on the **script**, not direct
  analysis of rendered video pixels. That is a meaningful difference from
  MoneyPrinterTurbo's video-conditioned cloud providers.

Setup and limitations: `docs/free-musicgen.md`.

### Animated subtitle parity

Creator Studio now supports `none`, `fade` and MoneyPrinterTurbo-style
`pop_spring` animations. The spring timing formula and original constants
are ported from upstream `app/services/video.py`. The implementation
uses libass animated scale transforms inside the existing FFmpeg finishing
pass, not an entire copy of MoviePy's text renderer. Other controls include
subtitle position, font size, text colour, highlight colour and optional
spoken-word highlighting. Exact highlighting depends on Edge TTS word
events or local Whisper alignment. Previous Creator projects default to
unanimated captions until the user selects a new animation.

This is a close animation adaptation, **not identical pixel-for-pixel
MoviePy composition**. Animated subtitles are rendered into a new export
file, leaving the original clipper and upstream video project cache intact.

## Advanced non-destructive editing controls

Creator Studio can now configure output aspect ratio (`portrait 720×1280`,
`landscape 1280×720`, `square 1080×1080`), footage fit (`cover` centre
crop or `contain` with black bars), footage playback speed (`0.5×`–`2×`)
and source assignment order (`script`, `reverse`, deterministic `shuffle`).
Preferences persist in `creator_edit_settings` and default to the original
Creator Studio values for all existing projects.

The original MIT-licensed MoneyPrinterTurbo revision renderer is not
modified. Its render settings already support custom canvas sizes. For
letterboxing/speed, `clipper/creator_edit_api.py` prepares safe local
intermediate video-only files with FFmpeg. Subsequent revisions can reuse
intermediates and the renderer's verified scene cache. Changes to editing
preferences invalidate the current Creator Studio render stage but preserve
narration, music, visuals, and all prior immutable revisions.

**Important semantics:** footage order changes which source *video* appears
with each scene; it does not reorder the spoken script or scene audio.
Speed applies to footage, looping/trimming as necessary to keep narration
length unchanged. Existing clipper APIs, stored clip projects and their
render paths are unaffected. Frame crop is centre-aligned; custom pan and
per-scene crop-position keyframes remain future work.

## Voice and soundtrack mix controls

Creator Studio's **Advanced video editing** panel now exposes separate saved
**narration volume (0–200%)** and **background music volume (0–100%)** choices.
They are passed to the FFmpeg/MoneyPrinterTurbo-compatible render manifest,
rather than modifying source WAV/MP3 files. Defaults remain 100% narration
and 15% background music. Music volume is ignored when no soundtrack is
selected.

Older stored editing settings with only `format`, `fit`, `speed` and
`order` are read with the new audio defaults. Re-saving identical settings
does not invalidate the export; changing the mix invalidates only the
render stage, preserving scripts, narrated audio and scene footage. Audio
controls adjust gain and do not apply loudness normalization, automatic
ducking or anti-clipping limiting. Review the mixed video before publishing.

## One-click automatic video production

`clipper/creator_auto_api.py` adds an **opt-in**, persistent, single-project
pipeline that reuses the existing Creator Studio endpoints and their validation.
It advances through script → narration → scene footage → music → captions →
MoneyPrinterTurbo-backed FFmpeg render → **review**. The user can see
progress and stage errors in the Creator Studio UI, and `creator_auto_jobs`
records the current step and completed stages.

The orchestrator **reuses saved work** (existing approved scripts, narration,
scene clips, and rendered exports), and does not rerun costly stages without
need. It requests the current free configured voice backend when narration
is missing. Automated stock acquisition requires explicit
`approve_stock_download=true` on each started job and an existing free stock
API key. It performs up to five bounded stock batches, stops with a clear
blocker on an unsuitable/missing shot, and never swaps existing manual footage.
The optional instrumental setting generates a basic local soundtrack when no
music has been selected; a separate **MusicGen neural music** option uses the
local model when installed. The user must explicitly authorize any first-use
model download. Both choices preserve existing selected music by default.
Whisper alignment is optional; captions otherwise use available Edge
word-boundary timings or the existing approximate fallback.

- Start: `POST /api/creator/projects/<id>/auto/start`
- Status: `GET /api/creator/projects/<id>/auto`
- Required body: `{"approve_stock_download": false, "music": "keep", "align_captions": false}`
- No publishing or paid provider is invoked.
- Interruptions are recorded in the database; starting again reuses completed
  stages. The current renderer may need to retry an interrupted FFmpeg stage.
- A one-click job **does not** guarantee footage relevance or video quality.
  Human review remains required before using or distributing the result.

**Current limitations:** this is a per-project orchestrator, not a multi-project
batch scheduler; it doesn't yet synthesize missing visual images or perform
video-conditioned music analysis. Local MusicGen is also accessible through the automatic workflow, but still
requires its optional dependencies and explicit first-use model-download
permission. Batch scheduling and real Windows hardware QA remain open. The automatic
music choice is the older free instrumental or an existing selected track.

## Local SD-Turbo AI image scenes

Creator Studio now has an optional free **local image generation** integration
(`clipper/local_imagegen.py`, `creator_image_api.py`). Diffusers loads
`stabilityai/sd-turbo` only if explicitly requested; first-use model downloads
require separate approval. Scene prompts come from saved script visual ideas.
A 512px generated PNG is converted by FFmpeg into a cinematic 720×1280 MP4
with a small zoom/pan, which the existing immutable MPT renderer can accept
as normal prepared footage.

The `creator_auto_api.py` pipeline optionally tries configured free stock
providers first, then fills only missing slots with local AI image scenes.
No original uploaded or accepted stock footage is replaced. Jobs persist
progress and preserve completed scenes after failure or restart. The UI also
allows requesting image scenes directly, outside the automatic pipeline.

This is **text-to-image plus camera motion**, not true neural text-to-video.
It has not been tested with real SD-Turbo weights on the user's Windows GPU;
CI verifies the orchestration with model doubles and **real FFmpeg video
animation**. SD-Turbo has separate model licensing. Setup and limitations:
`docs/free-images.md`.

## Still missing compared with MoneyPrinterTurbo

- Visual analysis of downloaded footage and more advanced semantic per-scene selection
- Practical model-tested text-to-video generation, plus real-world Windows image-model QA
- Alignment quality checks, richer subtitle animation and additional scene effects
- Multi-project batch queues, publish scheduling and social publishing
- A fully unified editor timeline with reversible B-roll and captions
- Video-conditioned soundtrack analysis and visual-quality checks for animated image scenes

These gaps should be ported or adapted independently behind opt-in paths
and tested before altering the existing clipper. None of the above missing
features should be called implemented until their tests and user flows work.

## Verification

`.github/workflows/creator-tests.yml` runs Python syntax checks and all
repository tests, including a real FFmpeg rendering smoke test when FFmpeg is
installed. The test must not be skipped when reporting a verified render.

Windows end-to-end verification on the owner's RTX 3060 system remains
necessary before calling the complete Creator Studio production-ready.
