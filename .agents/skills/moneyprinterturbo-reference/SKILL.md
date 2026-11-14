---
name: moneyprinterturbo-reference
description: Use when extending this AI Video Editor & Clipper with features inspired by harry0703/MoneyPrinterTurbo, especially stock footage and B-roll, scripting, voiceover, background music, compositing, batching, export, and publishing. This is a reference skill for modifying THIS repository, not for installing or running MoneyPrinterTurbo.
---

# MoneyPrinterTurbo reference skill for our video editor

## Source of truth

- Our product: https://github.com/AbdulRafay5/AI-Video-editor-and-Clipper
- Architectural reference: https://github.com/harry0703/MoneyPrinterTurbo
- Upstream's own execution skill (different purpose): https://github.com/harry0703/MoneyPrinterTurbo/blob/main/docs/skill/SKILL.md

Before each relevant implementation, read the **current** upstream source for the specific feature, not just its README. Check upstream versions and license when reusing code.

## Non-negotiable project requirements

1. Modify the existing `main` branch of **our** repository. Do not fork, replace, or create a second app.
2. Software and required services must cost **£0/$0**. Local Ollama, Whisper, FFmpeg, OpenCV and permitted free stock APIs are preferred. Never silently add paid APIs, trial-credit dependencies, subscriptions or cloud GPU services.
3. Preserve the existing transcript, clip/highlight, subtitle, facial reframing, campaign compliance, export and UI workflows. Do not erase or bypass campaign safety checks.
4. Create small, testable, isolated changes. Add regression tests for new behaviour. Run tests and rendering checks where tools and dependencies allow; clearly disclose anything not executed.
5. Commit meaningful tested changes with a human-readable message. Never claim a feature works end-to-end merely because code is committed.
6. Do not publish automatically without explicit user approval of the exact content and destination. Direct platform publication is not required for the offline editor.
7. Respect footage licences, creator attribution requirements, and campaign terms; don't generate fake speaker quotes or misleading edits.
8. No unneeded dependencies or mass copy-pasting of MoneyPrinterTurbo.

## Feature-to-reference map

| Feature | MoneyPrinterTurbo reference | Our extension point |
| --- | --- | --- |
| Stock footage search and download | `app/services/material.py`, `app/services/material_cache.py` | `clipper/broll_api.py`, new local match/selection service |
| Video assembly and transitions | `app/services/video.py`, `app/services/utils/video_effects.py` | `clipper/video.py`, `clipper/layout.py`, `clipper/jobs.py` |
| Script and topic-driven generation | `app/services/llm.py`, `app/services/task.py` | `clipper/llm.py`, separate creator-mode module |
| Voice synthesis | `app/services/voice.py` | new local TTS service and audio pipeline |
| Background music | `app/services/bgm.py` | new sound library, FFmpeg audio mixing |
| Subtitles and timing | `app/services/subtitle.py` | `clipper/captions.py` (retain edits and word timing) |
| Task batching and status | `app/services/task.py`, `app/services/generation_progress.py` | `clipper/jobs.py`, `clipper/db.py` |
| Publishing (optional only) | `app/services/upload_post.py` | offline export first; never make a paid service mandatory |

## Implementation method

1. Read the current relevant files in both repositories and describe actual differences.
2. Select the smallest useful improvement that works independently of paid services.
3. Extend existing Flask blueprints (new `clipper/*_api.py` modules auto-register) and optional `static/ext_*.js` modules where practical.
4. Validate input, file sizes, media metadata, permissions/licence terms, path safety, cancelled jobs, error handling, concurrent updates and ffmpeg output.
5. Test behaviour with unit tests and sample rendering when feasible; confirm existing clipper functionality is unaffected.
6. Update documentation and commit. Report changed files, tests run/not run, known limitations and next feature.

## Planned phased implementation

1. Local B-roll library (initial foundation already committed; validate and harden).
2. Context-aware B-roll selection, timecodes, preview and non-destructive rendering.
3. Better hooks and pacing using existing speech, without invented claims.
4. Copyright-safe music library, mixing, ducking and fades.
5. Batch variations with shared transcription/cache.
6. Original video creator mode: local scripts, free licensed footage, local TTS and video rendering.
7. Comprehensive validation and optional publishing integrations without paid dependencies.

## Licensing and safety

MoneyPrinterTurbo is MIT-licensed (upstream `LICENSE`). If any substantial upstream code is copied or adapted, preserve the required MIT copyright/license notice in the distributed codebase. Prefer independently adapted concepts to wholesale copying. Read licensing terms for any third-party media and API, separately from the upstream code license.

**Important distinction:** Upstream's `docs/skill/SKILL.md` runs MoneyPrinterTurbo itself and may involve paid providers or API credentials. Do **not** follow that execution skill's defaults in this repository. Our skill is for implementing free video-editing capabilities in our own codebase.
