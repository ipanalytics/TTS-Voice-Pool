"""Talking to the speech providers, and getting out of the way when one of them breaks.

The pool only helps if a broken provider is classified correctly, so the three failure
shapes are kept apart:

* ``quota`` — 429 / "quota exceeded": this key is resting, another key may still answer;
* ``dead`` — 401/403 / "API key not valid": no point retrying this key at all;
* ``error`` — anything else: one model failed, the model chain may still save the call.

The classification matters because the recovery is different: resting, replacing, or
retrying with another model. Treating all three as "failed" is how a working pool ends up
believing it has no keys.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from . import audio, config

GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
)
INTERACTIONS_URL = os.environ.get(
    "TTS_POOL_INTERACTIONS_URL",
    "https://generativelanguage.googleapis.com/v1beta/interactions",
)
PROMPT_HEAD = (
    "Synthesize speech of the transcript below in a warm female voice. Speak only the transcript."
)
PROMPT_TAIL = "#### TRANSCRIPT\n"
TIMEOUT = float(os.environ.get("TTS_POOL_TIMEOUT", "90"))


@dataclass
class SynthResult:
    ok: bool
    reason: str = ""
    seconds: float = 0.0
    audio_tokens: int = 0
    text_tokens: int = 0
    voice: str = ""
    model: str = ""
    path: Path | None = None
    notes: list[str] = field(default_factory=list)


def classify(status: int, detail: str) -> str:
    low = (detail or "").lower()
    if status == 429 or "quota" in low or "exceeded your current quota" in low:
        return "quota"
    if status in (401, 403) or "billing" in low or "api key not valid" in low:
        return "dead"
    return f"http {status}: {(detail or '')[:120]}"


def transcript(text: str) -> str:
    """Wrap the text the way the speech API documents it.

    Delivery notes do **not** go in here. A measured example: 74 characters of text plus
    notes came back as ~200 characters of speech, because the model read the notes aloud.
    Notes travel as a separate field (``speech_metadata``), and pace is enforced afterwards.
    """
    return f"{PROMPT_HEAD}\n\n{PROMPT_TAIL}{text}"


def style_supported(model: str) -> bool:
    if os.environ.get("TTS_POOL_PREPEND_STYLE") == "1":
        return False  # the legacy path: notes are glued to the text and may be spoken
    return "3.8" in model or os.environ.get("TTS_POOL_SEPARATE_STYLE") == "1"


# --------------------------------------------------------------------------- Gemini: prebuilt


def generate(
    key: str,
    text: str,
    out_path: Path,
    voice: str,
    style: str = "",
    model: str | None = None,
    rate: float = 10.0,
) -> SynthResult:
    model = model or config.model_chain()[0][0]
    part: dict = {"text": text}
    if style:
        if style_supported(model):
            part["speechMetadata"] = {"style": style}
        elif os.environ.get("TTS_POOL_PREPEND_STYLE") == "1":
            part["text"] = f"{style} {text}"
    body = json.dumps(
        {
            "contents": [{"parts": [part]}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}},
            },
        }
    ).encode()
    request = urllib.request.Request(
        GEMINI_URL.format(model=model, key=key),
        data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            payload = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return SynthResult(
            False, classify(exc.code, exc.read().decode(errors="replace")), model=model, voice=voice
        )
    except Exception as exc:
        return SynthResult(False, f"{type(exc).__name__}: {exc}", model=model, voice=voice)

    candidates = payload.get("candidates") or []
    parts = (candidates[0].get("content") or {}).get("parts") if candidates else None
    inline = ((parts[0].get("inlineData") or parts[0].get("inline_data")) if parts else None) or {}
    if not inline.get("data"):
        finish_reason = candidates[0].get("finishReason") if candidates else "no candidates"
        return SynthResult(False, f"empty response: {finish_reason}", model=model, voice=voice)
    usage = payload.get("usageMetadata") or {}
    return finish(
        base64.b64decode(inline["data"]),
        out_path,
        audio_tokens=int(usage.get("candidatesTokenCount") or 0),
        text_tokens=int(usage.get("promptTokenCount") or 0),
        voice=voice,
        model=model,
        chars=len(text),
        rate=rate,
    )


# ----------------------------------------------------------------------- Gemini: designed voice


def interactions(
    key: str,
    text: str,
    out_path: Path,
    voice: str,
    style: str = "",
    model: str | None = None,
    rate: float = 10.0,
) -> SynthResult:
    """A designed voice (``voice_...``) is only reachable through the interactions endpoint."""
    model = model or config.model_chain()[0][0]
    block: dict = {"type": "text", "text": transcript(text)}
    if style:
        block["annotations"] = [{"type": "speech_metadata", "style": style}]
    body = json.dumps(
        {
            "model": model,
            "input": [{"type": "user_input", "content": [block]}],
            "response_format": {"type": "audio"},
            "generation_config": {"speech_config": [{"voice": voice, "language": "ru-RU"}]},
        }
    ).encode()
    request = urllib.request.Request(
        INTERACTIONS_URL,
        data=body,
        headers={"Content-Type": "application/json", "x-goog-api-key": key},
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            payload = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return SynthResult(
            False, classify(exc.code, exc.read().decode(errors="replace")), model=model, voice=voice
        )
    except Exception as exc:
        return SynthResult(False, f"{type(exc).__name__}: {exc}", model=model, voice=voice)

    for step in payload.get("steps") or []:
        for item in step.get("content") or []:
            if item.get("type") == "audio" and item.get("data"):
                usage = payload.get("usage") or {}
                return finish(
                    base64.b64decode(item["data"]),
                    out_path,
                    audio_tokens=int(usage.get("total_output_tokens") or 0),
                    text_tokens=int(usage.get("total_input_tokens") or 0),
                    voice=voice,
                    model=model,
                    chars=len(text),
                    rate=rate,
                )
    return SynthResult(False, "empty response: no audio in steps", model=model, voice=voice)


# ------------------------------------------------------------------------------------- shared


def speak(
    key: str,
    text: str,
    out_path: Path,
    voice: str | None = None,
    style: str = "",
    model: str | None = None,
    rate: float = 10.0,
) -> SynthResult:
    """Try the requested voice, fall back to a prebuilt one, never leave a turn silent."""
    voice = voice or config.default_voice()
    if str(voice).startswith("voice_"):
        result = interactions(key, text, out_path, voice, style, model, rate)
        if result.ok or result.reason in ("quota", "dead"):
            return result
        fallback = interactions_fallback(key, text, out_path, style, model, rate, result)
        return fallback
    return generate(key, text, out_path, voice, style, model, rate)


def interactions_fallback(
    key: str,
    text: str,
    out_path: Path,
    style: str,
    model: str | None,
    rate: float,
    first: SynthResult,
) -> SynthResult:
    """A designed voice can expire; the episode still has to be voiced."""
    result = generate(key, text, out_path, config.FALLBACK_VOICE, style, model, rate)
    result.notes.append(f"designed voice failed ({first.reason}) — used {config.FALLBACK_VOICE}")
    return result


def finish(
    raw: bytes,
    out_path: Path,
    audio_tokens: int,
    text_tokens: int,
    voice: str,
    model: str,
    chars: int,
    rate: float,
) -> SynthResult:
    """Shared tail: detect the container, measure the audio, apply the pace, encode."""
    out_path = Path(out_path)
    if not audio.have_ffmpeg():
        return SynthResult(False, "ffmpeg is not installed", voice=voice, model=model)
    work = out_path.with_suffix(".source.wav")
    source, seconds = audio.write_source_audio(raw, work)
    result = SynthResult(
        True,
        "ok",
        seconds=seconds,
        voice=voice,
        model=model,
        audio_tokens=int(audio_tokens or round(seconds * 25)),
        text_tokens=int(text_tokens or 0),
    )
    factor = audio.pace_factor(chars, seconds, source)
    try:
        audio.encode(source, out_path, factor)
    except subprocess.CalledProcessError as exc:
        return SynthResult(False, f"ffmpeg failed: {exc}", voice=voice, model=model)
    finally:
        source.unlink(missing_ok=True)
    # Log the duration of the delivered file: before atempo it is a different number.
    measured = audio.probe_duration(out_path)
    if measured:
        result.seconds = measured
    result.path = out_path
    if abs(factor - 1.0) > 1e-3:
        result.notes.append(f"pace x{factor:.2f} applied")
    return result


# ------------------------------------------------------------------------------- free fallback


def edge_voice() -> str:
    return os.environ.get("TTS_POOL_EDGE_VOICE", "ru-RU-SvetlanaNeural")


def edge(text: str, out_path: Path) -> SynthResult:
    """Last resort: a free engine, so a broken key never means a silent episode.

    It runs in a subprocess on purpose: an optional dependency that is missing should cost
    one call, not the whole import.
    """
    out_path = Path(out_path)
    tmp = out_path.with_suffix(".edge.mp3")
    code = (
        "import asyncio, sys, edge_tts as e;"
        "asyncio.run(e.Communicate(sys.argv[1], sys.argv[3]).save(sys.argv[2]))"
    )
    try:
        proc = subprocess.run(
            [sys.executable, "-c", code, text, str(tmp), edge_voice()],
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        return SynthResult(False, f"edge_tts unavailable: {exc}")
    if proc.returncode == 0 and tmp.exists():
        try:
            audio.encode(tmp, out_path)
        finally:
            tmp.unlink(missing_ok=True)
        return SynthResult(
            True,
            "ok",
            seconds=audio.probe_duration(out_path) or 0.0,
            voice=edge_voice(),
            model="edge",
            path=out_path,
        )
    return SynthResult(False, f"edge_tts failed: {(proc.stderr or '')[-200:]}")
