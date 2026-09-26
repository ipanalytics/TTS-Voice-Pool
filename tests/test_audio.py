from __future__ import annotations

import pytest

from tts_voice_pool import audio


def test_wav_is_detected_from_the_bytes():
    assert audio.is_wav(b"RIFF\x00\x00\x00\x00WAVE") is True
    assert audio.is_wav(b"\x00\x01\x02\x03") is False


def test_raw_pcm_is_wrapped_in_a_header(tmp_path):
    pcm = b"\x00\x01" * 24_000  # one second of 24 kHz mono s16
    path = audio.pcm_to_wav(pcm, tmp_path / "a.wav")
    assert abs(audio.wav_seconds(path) - 1.0) < 0.01


def test_a_wav_is_not_wrapped_twice(tmp_path):
    src = audio.pcm_to_wav(b"\x00\x01" * 12_000, tmp_path / "src.wav")
    raw = src.read_bytes()
    path, seconds = audio.write_source_audio(raw, tmp_path / "out.wav")
    assert path.read_bytes() == raw
    assert abs(seconds - 0.5) < 0.01


def test_pcm_duration_comes_from_the_byte_count(tmp_path):
    _path, seconds = audio.write_source_audio(b"\x00\x01" * 48_000, tmp_path / "out.wav")
    assert abs(seconds - 2.0) < 0.01


def test_a_fast_take_is_stretched_but_not_beyond_the_floor():
    # 100 characters in 5 seconds of speech is 20 chars/s — far above the 12.3 ceiling
    assert audio.pace_factor(100, 5.0, base=1.0, ceiling=12.3) == pytest.approx(0.72)


def test_a_calm_take_keeps_the_configured_tempo():
    assert audio.pace_factor(50, 10.0, base=0.94, ceiling=12.3) == pytest.approx(0.94)


def test_no_ceiling_means_no_stretching():
    assert audio.pace_factor(100, 1.0, base=1.0, ceiling=0.0) == 1.0


def test_missing_numbers_are_not_a_reason_to_fail():
    assert audio.pace_factor(0, 0.0, base=1.05, ceiling=12.3) == pytest.approx(1.05)


def test_the_tempo_filter_is_omitted_when_it_changes_nothing():
    assert audio.tempo_filter(1.0) == []
    assert audio.tempo_filter(0.94) == ["-filter:a", "atempo=0.940"]


def test_the_codec_follows_the_requested_extension(tmp_path):
    assert "libmp3lame" in audio.codec_args(tmp_path / "voice.mp3")
    assert "libopus" in audio.codec_args(tmp_path / "voice.ogg")
