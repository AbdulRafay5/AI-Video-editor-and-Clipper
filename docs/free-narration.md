# Free narration in Creator Studio

Creator Studio supports two free neural voice engines and keeps its existing
Windows SAPI / Piper offline fallback.

## Kokoro (preferred: local, offline after setup)

Kokoro is an open-source 82M-parameter neural TTS model. Use a local
OpenAI-compatible Kokoro server, such as [Kokoro-FastAPI](https://github.com/remsky/Kokoro-FastAPI).
Follow that project's setup instructions and obtain the model/voice files
locally. **No cloud API or billing account is needed.** The initial model
download requires an Internet connection.

Start the server so that its OpenAI-compatible API is available at:

```
http://127.0.0.1:8880/v1
```

Optional local environment variable: `KOKORO_URL` (loopback HTTP only).
The app queries `/models` to detect the server and posts the selected
voice to `/audio/speech` using `response_format: mp3`. FFmpeg converts
the returned audio into uncompressed WAV for reliable scene timing.

After starting the server, refresh Creator Studio, choose **kokoro** and
one of the listed English voice presets (such as `af_heart`).

Kokoro availability depends on the installed server and model. It may
require additional RAM/CPU. The app deliberately does **not** install or
start third-party model servers behind your back.

## Edge TTS (free, Internet required)

Install the optional dependency into your editor's Python environment:

```powershell
python -m pip install -r requirements-voice.txt
```

Restart the app, open Creator Studio, select **edge**, and choose a voice
such as `en-GB-SoniaNeural` or `en-US-AriaNeural`.

Edge TTS uses Microsoft's online neural voices. It is free to access
without an API key but **is not offline**; it depends on Internet access
and Microsoft's service availability/terms.

## Existing voice engines

- **Piper:** Set `PIPER_MODEL` to the full path of a locally installed
  voice model and ensure the `piper` executable is available in PATH.
- **Windows SAPI:** Built into compatible Windows systems, offline but
  often less natural-sounding than neural voices.
- **FFmpeg:** Required for output conversion and joining segments.

## Production flow

1. Generate or save the script.
2. Select the voice engine and voice in Creator Studio.
3. Generate narration. The hook, each scene and the closing are rendered
   separately, combined, and stored under `data/creator/<id>/narration/`.
4. Listen to the complete WAV or individual scene WAVs.
5. Regenerate narration after editing a script. Future production stages
   are marked pending when the upstream audio changes.

The feature makes no paid calls. All generated scenes still require
visuals, captions, music and final rendering before they become a video.

## Your own recorded narration (offline)

Creator Studio also accepts custom, user-owned WAV recordings. Save the script first,
then use **Or upload your own recorded voice** in the narration section.

Supply one WAV file for each script part in order: hook, every scene, and
closing. Each file must be at most 25 MB; total upload is limited to 150 MB.
FFmpeg converts the recordings to mono 22,050 Hz PCM and joins them into a
single WAV. The project stores per-scene audio and measured durations in the
same format used by synthetic narration. No TTS engine, account or paid service
is required for this mode.

A successful upload replaces the project's active narration and invalidates
existing captions and final render. The original script and visual assets remain
unchanged. Upload failures preserve the previously active narration. For
accurate word-by-word captions, run the optional local caption alignment stage
after importing the recording.

Only upload voices you recorded yourself or have permission to use. This
feature **imports recordings**; it does not train, imitate, or clone voices.
