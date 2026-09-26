"""From bytes on the wire to a file a chat client will play — and to a pace a human can bear.

Three things here were found the hard way, and each of them is a bug that is invisible in a
short demo:

1. **The same model returns different containers.** One model answers with raw PCM
   (``audio/l16``), another with a finished WAV. Wrapping a WAV in a second header produces
   noise, so the format is detected from the bytes, not from the model name.
2. **"Characters per second" over the whole file lies.** A long episode can average 12.3
   chars/s across the text and still be read at 16–17 inside the sentences — and *that* is
   what a listener calls "too fast". Measuring the speech (silence removed with ffmpeg) and
   capping the *articulation* rate fixed it.
3. **The duration in the log must be the duration of the delivered file.** Before ``atempo``
   it is a different number, and a report built on the wrong one quietly drifts.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import wave
from pathlib import Path

from . import config

SPEECH_RATE_FLOOR = 0.72  # stretch no further than this; deeper and it sounds smeared
SILENCE_NOISE = "-35dB"
SILENCE_MIN = 0.35
SAMPLE_RATE = 24000
BYTES_PER_SAMPLE = 2


def have_ffmpeg() -> bool:
    return bool(shutil.which("ffmpeg"))


def is_wav(raw: bytes) -> bool:
    return raw[:4] == b"RIFF"


def pcm_to_wav(pcm: bytes, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(BYTES_PER_SAMPLE)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes(pcm)
    return path


def write_source_audio(raw: bytes, path: Path) -> tuple[Path, float]:
    """Write the incoming audio as a WAV and return (path, seconds of audio)."""
    if is_wav(raw):
        path.write_bytes(raw)
        return path, wav_seconds(path)
    pcm = raw
    pcm_to_wav(pcm, path)
    return path, len(pcm) / BYTES_PER_SAMPLE / SAMPLE_RATE


def wav_seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as handle:
        frames = handle.getnframes()
        rate = handle.getframerate() or SAMPLE_RATE
    return frames / float(rate)


def articulation(chars: int, wav_path: Path, total_seconds: float) -> float:
    """Characters per second of *speech*, silences excluded (0.0 if it cannot be measured)."""
    if not chars or not total_seconds or not have_ffmpeg():
        return 0.0
    try:
        proc = subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-nostats",
                "-i",
                str(wav_path),
                "-af",
                f"silencedetect=noise={SILENCE_NOISE}:d={SILENCE_MIN}",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            text=True,
        )
    except OSError:
        return 0.0
    silence = sum(
        float(value) for value in re.findall(r"silence_duration: ([0-9.]+)", proc.stderr or "")
    )
    speech = max(0.2, total_seconds - silence)
    return chars / speech


def pace_factor(
    chars: int,
    raw_seconds: float,
    wav_path: Path | None = None,
    base: float | None = None,
    ceiling: float | None = None,
) -> float:
    """atempo multiplier: the configured tempo, but never faster than the ceiling.

    A fast take is stretched out to the ceiling; a calm one is left alone. Nothing is
    stretched below ``SPEECH_RATE_FLOOR``, because past that the artefact is audible.
    """
    base = config.tempo() if base is None else base
    ceiling = config.pace_ceiling() if ceiling is None else ceiling
    if not (ceiling and chars and raw_seconds):
        return base
    rate = articulation(chars, wav_path, raw_seconds) if wav_path else 0.0
    rate = rate or (chars / raw_seconds)
    if rate <= ceiling:
        return base
    return min(base, max(SPEECH_RATE_FLOOR, ceiling / rate))


def tempo_filter(factor: float | None = None) -> list[str]:
    value = config.tempo() if factor is None else factor
    return ["-filter:a", f"atempo={value:.3f}"] if abs(value - 1.0) > 1e-3 else []


def codec_args(path: Path) -> list[str]:
    """Encode to exactly the container the caller asked for, not to whatever is default."""
    if str(path).lower().endswith(".mp3"):
        return ["-c:a", "libmp3lame", "-b:a", "64k"]
    return ["-c:a", "libopus", "-b:a", "48k"]


def encode(wav_path: Path, out_path: Path, factor: float | None = None) -> Path:
    """WAV → requested container, with the pace applied in the same pass."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(wav_path),
            *tempo_filter(factor),
            *codec_args(out_path),
            str(out_path),
        ],
        check=True,
    )
    return out_path


def probe_duration(path: Path) -> float | None:
    try:
        proc = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "csv=p=0",
                str(path),
            ],
            capture_output=True,
            text=True,
        )
        return float(proc.stdout.strip())
    except (OSError, ValueError):
        return None
