"""TTS Voice Pool — one call, several speech providers, and an honest exit code.

Why this exists: a scheduled job that has to speak has nobody to ask when the voice fails.
So the pool keeps several API keys, remembers which one worked (and which is resting after a
quota error), falls back to a prebuilt voice when a designed one expires, enforces a pace a
human can listen to, and finally falls back to a free engine — because a silent episode is
worse than a different voice.

    from tts_voice_pool import say

    code = say("Good morning.", "briefing.ogg")
    # 0 spoken (by any provider), 2 nothing could speak at all

Everything else (the CLI, the pace ceiling, the usage ledger) is documented in README.md.
"""

from __future__ import annotations

from pathlib import Path

from . import audio, config, ledger, speech
from .cli import say as _say
from .pool import KeyState, Pool, parse_keys
from .speech import SynthResult

__version__ = "1.0.0"

__all__ = [
    "KeyState",
    "Pool",
    "SynthResult",
    "audio",
    "config",
    "ledger",
    "parse_keys",
    "say",
    "speech",
]


def say(
    text: str,
    out: str | Path,
    voice: str | None = None,
    style: str | None = None,
    model: str | None = None,
    rate: float = 10.0,
) -> int:
    """Speak ``text`` into ``out`` and return the process exit code (0 spoken, 2 not).

    This is the same engine the CLI uses: key rotation, model chain, budget check, pace,
    free fallback. It never raises for provider errors — a failed voice is a return code,
    not an exception, because the caller is usually a scheduled job.
    """
    pool = Pool.load()
    if not pool:
        print(f"the key pool is empty: {config.keys_file()} (or GEMINI_API_KEYS)")
        return 2
    return _say(
        pool,
        text,
        Path(out),
        voice or config.default_voice(),
        config.style() if style is None else style,
        model,
        rate,
    )
