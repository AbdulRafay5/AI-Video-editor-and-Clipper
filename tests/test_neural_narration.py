"""Regression tests for free neural narration; no Internet or model installation."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from clipper import local_narration as local, neural_narration as neural


class NeuralVoiceTests(unittest.TestCase):
    def test_kokoro_endpoint_must_be_local(self):
        with patch.dict("os.environ", {"KOKORO_URL": "https://example.org/v1"}):
            with self.assertRaises(neural.NeuralVoiceError):
                neural._kokoro_url()
        with patch.dict("os.environ", {"KOKORO_URL": "http://127.0.0.1:8880/v1"}):
            self.assertEqual(neural._kokoro_url(), "http://127.0.0.1:8880/v1")
        with patch.dict("os.environ", {"KOKORO_URL": "http://127.0.0.1:8880@evil.org/v1"}):
            with self.assertRaises(neural.NeuralVoiceError):
                neural._kokoro_url()

    def test_no_kokoro_available_still_shows_voices(self):
        with patch.object(neural, "_kokoro_request", side_effect=OSError("offline")):
            choices = neural.available_voices()
        self.assertEqual(choices[0]["name"], "kokoro")
        self.assertFalse(choices[0]["ready"])
        self.assertEqual(choices[1]["name"], "edge")
        self.assertTrue(choices[1]["online"])

    def test_prefer_local_kokoro_when_available(self):
        choices = [
            {"name": "kokoro", "ready": True, "voice": "af_heart",
             "voices": ["af_heart"], "online": False},
            {"name": "edge", "ready": True, "voice": "en-GB-SoniaNeural",
             "voices": ["en-GB-SoniaNeural"], "online": True},
        ]
        with patch.object(local, "available_backends", return_value=choices):
            selected = local.select_backend()
            self.assertEqual(selected["name"], "kokoro")
            self.assertEqual(local.select_backend("edge")["name"], "edge")
            with self.assertRaises(local.NarrationError):
                local.select_backend("edge", "not-an-edge-voice")

    def test_neural_voice_dispatches_and_saves_wav(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "speech.wav"
            chosen = {"name": "kokoro", "ready": True, "voice": "af_heart"}
            with patch.object(neural, "speak_neural", return_value=1.25) as speak:
                seconds = local.synthesize("This is a narration", target, chosen)
            self.assertEqual(seconds, 1.25)
            speak.assert_called_once_with("This is a narration", target, chosen)

    def test_kokoro_sends_voice_and_converts_audio(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "speech.wav"
            def fake_request(path, payload=None, timeout=None):
                self.assertEqual(path, "/audio/speech")
                self.assertEqual(payload["voice"], "af_heart")
                self.assertEqual(payload["model"], "kokoro")
                return b"x" * 256
            def fake_convert(source, output):
                self.assertTrue(source.is_file())
                output.write_bytes(b"fake WAV")
            with patch.object(neural, "_kokoro_request", side_effect=fake_request), \
                 patch.object(neural, "_to_wav", side_effect=fake_convert), \
                 patch.object(local, "wav_duration", return_value=0.8):
                duration = neural.speak_neural("Hello there", target,
                                               {"name": "kokoro", "voice": "af_heart"})
            self.assertEqual(duration, 0.8)
            self.assertTrue(target.is_file())
            self.assertFalse(target.with_suffix(".mp3").exists())

    def test_edge_stream_captures_real_word_boundaries(self):
        import asyncio
        import sys
        import types
        async def events():
            yield {"type": "audio", "data": b"x" * 256}
            yield {"type": "WordBoundary", "offset": 1500000,
                   "duration": 3500000, "text": "Hello"}
            yield {"type": "WordBoundary", "offset": 5000000,
                   "duration": 4000000, "text": "world"}
        voice = types.SimpleNamespace(stream=lambda: events())
        fake = types.SimpleNamespace(Communicate=lambda *args, **kwargs: voice)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "edge.mp3"
            with patch.dict(sys.modules, {"edge_tts": fake}):
                words = asyncio.run(neural._edge_stream("Hello world", "en-GB-SoniaNeural", path))
            self.assertEqual(len(words), 2)
            self.assertAlmostEqual(words[0]["start"], 0.15)
            self.assertAlmostEqual(words[0]["end"], 0.5)
            self.assertEqual(path.stat().st_size, 256)

    def test_native_pace_validation_preserves_offline_defaults(self):
        local_voice={"name":"kokoro","voice":"af_heart","ready":True}
        self.assertEqual(local.with_pace(local_voice,0.8)["pace"],0.8)
        self.assertEqual(local.with_pace(local_voice,1.2)["pace"],1.2)
        self.assertEqual(local.with_pace({"name":"windows-sapi","ready":True},1)["pace"],1)
        for invalid in (True, "1.2", 0.3, 3, None):
            with self.assertRaises(local.NarrationError):
                local.with_pace(local_voice,invalid)
        with self.assertRaises(local.NarrationError):
            local.with_pace({"name":"piper","ready":True},1.2)

    def test_kokoro_native_pace_is_forwarded_to_local_server(self):
        with tempfile.TemporaryDirectory() as folder:
            target=Path(folder)/"speech.wav"
            def fake_request(path,payload=None,timeout=None):
                self.assertEqual(payload["speed"],0.8)
                return b"x"*256
            with patch.object(neural,"_kokoro_request",side_effect=fake_request), \
                 patch.object(neural,"_to_wav",side_effect=lambda a,b: b.write_bytes(b"test")), \
                 patch.object(local,"wav_duration",return_value=0.9):
                self.assertEqual(neural.speak_neural("Hello",target,{
                    "name":"kokoro","voice":"af_heart","pace":0.8}),0.9)

    def test_edge_native_pace_uses_rate_option(self):
        import asyncio
        import sys
        import types
        observed=[]
        async def fake_stream():
            yield {"type":"audio","data":b"x"*256}
        def fake_communicate(text,voice,rate):
            observed.append(rate)
            return types.SimpleNamespace(stream=fake_stream)
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"paced.mp3"
            with patch.dict(sys.modules,{"edge_tts":types.SimpleNamespace(
                Communicate=fake_communicate)}):
                asyncio.run(neural._edge_stream("Hello","en-GB-SoniaNeural",path,1.2))
            self.assertEqual(observed,["+20%"])

    def test_invalid_voice_never_sends_request(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(neural, "_kokoro_request") as send:
                with self.assertRaises(neural.NeuralVoiceError):
                    neural.speak_neural("Hello", Path(folder) / "a.wav",
                                        {"name": "kokoro", "voice": "invalid"})
            send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
