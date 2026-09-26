from __future__ import annotations

import base64
import json
from pathlib import Path

from tts_voice_pool import config, speech

PCM = b"\x00\x01" * 2400


class _Response:
    """Stands in for the urlopen context manager."""

    def __init__(self, payload: dict):
        self.payload = payload

    def read(self) -> bytes:
        return json.dumps(self.payload).encode()

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *exc) -> bool:
        return False


def test_the_three_failure_shapes_stay_apart():
    assert speech.classify(429, "") == "quota"
    assert speech.classify(400, "You exceeded your current quota") == "quota"
    assert speech.classify(403, "API key not valid") == "dead"
    assert speech.classify(401, "billing is not enabled") == "dead"
    assert speech.classify(500, "boom").startswith("http 500")


def test_delivery_notes_never_travel_inside_the_transcript():
    text = speech.transcript("Guten Morgen.")
    assert "Guten Morgen." in text
    assert "TRANSCRIPT" in text
    assert "playful" not in text


def test_notes_go_in_a_separate_field_only_where_it_is_supported(monkeypatch):
    assert speech.style_supported("gemini-3.8-flash-lite-tts") is True
    assert speech.style_supported("gemini-2.5-flash-preview-tts") is False
    monkeypatch.setenv("TTS_POOL_PREPEND_STYLE", "1")
    assert speech.style_supported("gemini-3.8-flash-lite-tts") is False


def test_generate_reads_the_audio_and_the_usage(monkeypatch):
    seen: dict = {}

    def fake_finish(raw, out_path, **kw):
        seen["raw"] = raw
        seen.update(kw)
        return speech.SynthResult(True, "ok")

    monkeypatch.setattr(speech, "finish", fake_finish)
    payload = {
        "candidates": [
            {"content": {"parts": [{"inlineData": {"data": base64.b64encode(PCM).decode()}}]}}
        ],
        "usageMetadata": {"candidatesTokenCount": 1200, "promptTokenCount": 30},
    }
    monkeypatch.setattr(speech.urllib.request, "urlopen", lambda *a, **k: _Response(payload))

    result = speech.generate("K", "hallo", Path("/tmp/x.ogg"), "Kore", model="m", rate=6.0)

    assert result.ok and seen["raw"] == PCM
    assert seen["audio_tokens"] == 1200 and seen["text_tokens"] == 30
    assert seen["chars"] == len("hallo") and seen["rate"] == 6.0


def test_an_empty_response_is_an_error_and_says_why(monkeypatch):
    payload = {"candidates": [{"finishReason": "SAFETY"}]}
    monkeypatch.setattr(speech.urllib.request, "urlopen", lambda *a, **k: _Response(payload))
    result = speech.generate("K", "hi", Path("/tmp/x.ogg"), "Kore")
    assert result.ok is False and "SAFETY" in result.reason


def test_a_designed_voice_that_is_out_of_quota_does_not_burn_another_voice(monkeypatch):
    calls: list = []
    monkeypatch.setattr(speech, "interactions", lambda *a, **k: speech.SynthResult(False, "quota"))
    monkeypatch.setattr(
        speech, "generate", lambda *a, **k: calls.append(k) or speech.SynthResult(True)
    )
    result = speech.speak("K", "hi", Path("/tmp/x.ogg"), "voice_abc")
    assert result.reason == "quota"
    assert calls == []


def test_a_broken_designed_voice_falls_back_to_a_prebuilt_one(monkeypatch):
    seen: dict = {}
    monkeypatch.setattr(
        speech, "interactions", lambda *a, **k: speech.SynthResult(False, "http 500: nope")
    )

    def fake_generate(key, text, out_path, voice, *a, **k):
        seen["voice"] = voice
        return speech.SynthResult(True, "ok")

    monkeypatch.setattr(speech, "generate", fake_generate)
    result = speech.speak("K", "hi", Path("/tmp/x.ogg"), "voice_abc")
    assert result.ok and seen["voice"] == config.FALLBACK_VOICE
    assert any("designed voice failed" in note for note in result.notes)
