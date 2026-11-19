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

## On-demand scene footage preview

Each populated opening/scene/closing slot in Creator Studio now has an
optional **Preview scene footage** player. A dedicated per-project route
streams only the saved file assigned to that slot (MP4, MOV or MKV) and
supports HTTP byte ranges for browser seeking. It does not accept a caller
filename or arbitrary filesystem path; missing clips and stale scene slots
return 404.

The player can seek to the saved scene **Clip in** position and pause/reset
at **Clip out**. It shows source footage, not the composited render: crop,
speed, transitions, captions, narration and music require a rendered export
for an accurate final preview. Some MOV/MKV codecs are unsupported by browsers
even though the local FFmpeg renderer can process them. Previews are loaded
only when opened, and closing them releases the player source.

## Review and reject stock B-roll

Each auto-sourced footage slot now offers **Reject this stock clip**.
Rejection clears only that slot's active clip assignment, preserves the
downloaded file for immutable older revisions, invalidates the current render,
and records the provider/asset ID in the visual-stage artifact. Future stock
batches skip rejected assets, allowing an alternate result instead of repeating
the same unsuitable shot. Manual footage is never eligible for the stock
rejection action, and the action is blocked during active generation.

This is a user-driven review control, not computer-vision recognition.
Users may upload their own replacement or explicitly authorize another
free-provider stock search. Provider availability is not guaranteed.

## Smarter free B-roll selection (metadata, not image recognition)

Creator Studio now scores candidate clips deterministically by portrait aspect,
minimum usable portrait-HD resolution, and whether their duration is long enough
for a typical scene. It prefers suitable moderate-size HD renditions over
large landscape or tiny preview clips. Download verification failure now
tries a different stock result rather than stopping after the first match.

Chosen provider asset IDs and safe source URLs are recorded in the existing
visual-stage metadata, allowing later sourcing jobs to avoid reusing previously
selected clips within the project. Existing manually selected clips are never
replaced. The selector does **not** inspect frames, understand video semantics,
or guarantee scene relevance or licensing: it is a more robust metadata ranking
and retry strategy, not a computer-vision quality model. It never calls a
paid provider or downloads content without approval.

## Final MP4 integrity gate

After rendering and optional finishing, Creator Studio now runs local FFprobe
against the output MP4 before marking the render stage complete. The gate
requires a decodable container, a usable video stream with positive dimensions,
an audio stream, and a finite positive duration within a tolerance of the
measured narration segments. It stores the checked duration, resolution and
file size with the render artifact. The render status API exposes this metadata,
and download/preview URLs are withheld if a verified file's size has changed
since promotion. Legacy export records without this metadata remain readable
for backwards compatibility.

The check catches missing tracks, broken containers and gross duration errors.
It is **not** frame-by-frame visual inspection, loudness measurement, a
cryptographic integrity guarantee or complete hardware acceptance testing.
FFprobe must be available locally. The original immutable renderer and older
render caches remain untouched.

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

## Per-scene playback and framing

Creator Studio now allows an independent **footage playback speed** (0.5×–2×)
and **framing mode** (crop to fill or letterbox) for the opening, each scene,
and the ending. Empty scene settings inherit the project's global editing
preferences. Choices are saved separately per project in
`creator_scene_effects`; older projects retain the previous defaults.

Per-scene settings are applied only while preparing footage through FFmpeg.
The script, generated narration, subtitle chronology, and speech-aligned scene
durations are not modified. The renderer may repeat/trim the adjusted footage
to match narration. Source files are never overwritten; prepared clips have
content-and-setting keyed cache files. Invalid speeds, unsupported framing
modes, and non-existent scene slots are rejected. A setting change invalidates
the final render, but preserves other production stages.

## Non-destructive per-scene clip trimming

Each script slot can now save an optional **Clip in** start time and **Clip out**
end time in seconds. Both are local source-footage coordinates (0–3600s),
not changes to narration or subtitle timing. A supplied end must be strictly
later than the start. FFmpeg extracts the chosen visual range, then applies
the existing speed, framing and focal-point controls. The downstream renderer
continues fitting footage to measured speech duration (repeating or shortening
visuals when necessary), so trimming does not move captions or voice segments.

The trim settings are validated and persisted with the existing per-scene
effects. They enter prepared-footage cache identities, and a changed trim only
invalidates the final render; the original media and prior immutable revisions
remain intact. Use blank fields to keep the complete source clip. This is a
scene-level cut, not a full draggable timeline, frame-exact editor or
multiple B-roll cuts within one scene. Real FFmpeg duration regression tests
are included, but the complete Windows workflow still requires acceptance QA.

## Per-scene crop focal points

Creator Studio now offers independent horizontal (left/centre/right) and
vertical (top/centre/bottom) crop focus controls for each scene, including
the opening and closing. Positions are normalized to 0.0–1.0 and used by
the local FFmpeg crop expression when **Crop to fill** is active. This is a
static focal point per scene, not animated pan/keyframing or subject tracking.
Letterbox mode does not crop and therefore ignores the focal-point setting.

The existing centre-crop defaults and unmodified-source fast path remain
unchanged. Non-default focal points prepare a cached, immutable video
intermediate. Different focal points have different cache identities.
Saving effects invalidates only the final render, not narration, captions
or original footage. The FFmpeg smoke tests compare real left/right results
without requiring GPU model inference.

## Manual scene-by-scene footage reuse

The Advanced video editing panel includes **manual scene footage assignment**
for opening, every scene and closing. Select a source slot to use a clip already
saved in the same project; sources can be reused in multiple destinations. A
blank/default selection leaves the existing script/reverse/shuffle rule intact.
Manual overrides take precedence over that global order choice.

Only validated project scene identifiers are accepted. No arbitrary filesystem
paths are used and no generated footage is deleted or rewritten. The final
render is invalidated when the mapping changes, while narrated speech,
captions, and source media are preserved. Each scene keeps its original spoken
audio and duration. A manual source needs its footage saved before rendering.
Stale scene IDs are ignored after a script changes; review mappings after
adding or removing scenes.

## Adjustable narration pacing

Creator Studio supports **relaxed 0.8×**, **normal 1.0×**, and **energetic 1.2×**
speaking rates for configured Kokoro and Edge TTS voices. These use the voice
engine's native speed/rate controls, rather than post-processing an existing
recording. The same selected rate is used for the disposable voice preview and
the full generation run. Measured output WAV durations remain the source of
scene offsets and caption alignment; Edge word boundaries are collected from
the paced speech stream.

Piper and Windows SAPI continue at their original default speaking speed;
non-default pace for those engines is rejected rather than silently ignored.
Existing projects and automation calls without a pace continue at **1.0×**.
Changing speaking pace requires regenerating narration and its derived captions
and export. It cannot be applied retroactively to a previously generated WAV.
Real provider synthesis and Windows hardware acceptance testing remain pending.

## Narration voice previews

Creator Studio now exposes **Preview selected voice** alongside the local
Kokoro/Piper/Windows and free online Edge TTS voice choices. The preview
synthesises one short, predefined phrase using the selected ready backend
and returns a disposable WAV file for immediate browser playback.

Previewing does not change the saved script, narration artifacts, subtitle
timing, existing exports or project status. Preview requests do not accept
arbitrary dialogue or filesystem paths, and concurrent voice previews are
rejected rather than overloading a small local machine. First-run model setup
and backend availability are unchanged. This is a sample of the selected
voice, not a guarantee of quality for a long script. Windows live validation
is still pending.

## Voice and soundtrack mix controls

Creator Studio's **Advanced video editing** panel now exposes separate saved
**narration volume (0–200%)**, **background music volume (0–100%)**, and optional **automatic music ducking**.
They are passed to the FFmpeg/MoneyPrinterTurbo-compatible render manifest,
rather than modifying source WAV/MP3 files. Defaults remain 100% narration
and 15% background music. Music volume is ignored when no soundtrack is
selected.

Older stored editing settings with only `format`, `fit`, `speed` and
`order` are read with the new audio defaults. Re-saving identical settings
does not invalidate the export; changing the mix invalidates only the
render stage, preserving scripts, narrated audio and scene footage. Audio
controls adjust gain and do not apply loudness normalization or anti-clipping
limiting. Optional automatic ducking uses an FFmpeg sidechain compressor
driven by the narration audio, with fixed threshold/attack/release settings.
It reduces music during speech and raises it after speech stops; it does not
analyze video content or guarantee a broadcast-quality mix. Ducking is
disabled by default for old and new projects. Review the result before publishing.

## Pausable multi-project batch queue

The local sequential batch producer already supports 2–8 projects,
independent status tracking, retrying incomplete projects, and review-only
completion. Batches now have **Pause after current video** and
**Resume remaining videos** controls in Creator Studio.

Pausing changes the batch to `paused` and prevents the coordinator from
starting another queued project. The currently active project is **not**
cancelled, because interrupting an FFmpeg or model stage could leave
incomplete media. The user can resume once that item finishes; the API
rejects an early resume to avoid starting concurrent batch workers.
Previously completed items and immutable render artifacts are preserved.

This is a manual pause control, not a scheduling system, GPU resource
manager, or automatic upload feature. Batch processing still runs
sequentially to avoid competing inference workloads on a small local GPU.
Regression tests cover pause, duplicate-pause rejection and safe resumption.
Full Windows acceptance testing is still required.

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

## Stabilization: trim bounds and project media cleanup

Per-scene trim in/out points are checked against the source video's
FFprobe-measured duration before preparing a new clip. The editor returns a
specific error for a start at or beyond the end of the video, an out point
past its duration, or unreadable footage. Source files remain unchanged.
Trims still use the narration timeline for output pacing.

Deleting a Creator Studio project now also removes its project-owned media
directory under `data/creator/<project-id>/`, including cached intermediate
outputs and rendered MP4s. **Export or back up any videos you want to keep
before deleting a project.** The deletion endpoint refuses projects with
active generation, active automatic production, or queued/running batch items.
It never takes a filesystem path from the request, rejects symlinked project
directories for cleanup, and returns a warning if database deletion succeeds
but disk cleanup fails. Unrelated projects' media is not touched.

Tests isolate Creator data in temporary folders and cover cleanup,
active-job protection and out-of-range trim validation. These changes still
require full automated and Windows acceptance testing.


## Batch timeout containment (no unsafe forced cancellation)

The batch coordinator waits up to 24 hours per automated project before
entering a **paused** state. Importantly, this deadline does **not** mark a
still-running worker complete or start the next project. It keeps the current
batch item active until the underlying automation job reaches a terminal state.
If a coordinator reports failure while an independent stage (rendering, images,
stock, etc.) is still busy, the batch also pauses and waits for the stage to
become idle before releasing the item.

This prevents overlapping timed-out tasks on one local GPU. It is **not** a
true process cancellation feature: a permanently stuck inference or FFmpeg
process can still require an app restart, and pausing doesn't kill running work.
Do not resume until the active item has stopped. Completed videos are retained,
and incomplete projects can be retried using the existing batch controls.
Automated unit tests model coordinator deadline and lingering-stage scenarios;
real Windows tests are still needed.

## AI-only automation retries and local GPU use

Automation's `visual_mode=ai` now checks whether **all** current script
slots already have locally saved AI-generated MP4 files with
`local-sd-turbo` source metadata and a completed visuals stage. If so,
a retry following a music, captions, or render failure **reuses** those scenes
instead of regenerating them. This avoids unnecessary GPU work and preserves
the rendered AI footage already selected.

A first switch into AI-only mode still regenerates all scenes, even when
stock or user-uploaded clips are present. Missing files, incomplete scene
sets, mismatched source metadata, or invalidated visuals trigger a fresh
AI-only generation run. This is a source-file existence check and metadata
validation, not proof that existing video files are decodable. Full
end-to-end rendering and Windows hardware acceptance testing remain
outstanding.

## Render readiness after media and caption edits

Creator Studio now uses a shared render-invalidation helper for footage
upload/replacement, soundtrack upload/removal/generation/preset selection and
caption finishing settings. These operations invalidate the prior completed
render stage and reset an otherwise stale `render_ready` project status to
`narration_ready` (or `script_ready` if narration is unavailable).

The existing source footage, narration, cached immutable files and generated
music are retained unless explicitly replaced or removed. A new export must
be rendered before the project is marked ready again. New regression tests
cover settings changes, generated soundtrack changes and music removal.
Full CI and local Windows FFmpeg acceptance testing remain outstanding.
