"""Where the pool keeps its files and how it is configured.

Everything is read from the environment with a sensible default, so the package can be used
as a library, as a CLI, or from a scheduled job without touching code. A missing file is
never an error: each knob falls back to the value that has worked in production.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

DEFAULT_MODELS: tuple[tuple[str, float], ...] = (
    # (model, USD per 1M audio tokens). 25 audio tokens ≈ 1 second on this model.
    ("gemini-2.5-flash-preview-tts", 10.0),
)

DEFAULT_PACE_CEILING = 12.3  # characters per second of actual speech
DEFAULT_TEMPO = 1.0  # atempo multiplier
DEFAULT_VOICE = "Kore"  # a prebuilt Gemini voice
FALLBACK_VOICE = "Sulafat"  # used when a designed voice is gone or expired
COOLDOWN_SECONDS = 300  # rest after a 429: no hammering, no half-hour blackout


def home() -> Path:
    """Base directory: $TTS_POOL_HOME, then $HERMES_HOME, then ~/.hermes."""
    for var in ("TTS_POOL_HOME", "HERMES_HOME"):
        raw = os.environ.get(var)
        if raw:
            return Path(raw)
    return Path.home() / ".hermes"


def data_dir() -> Path:
    return home() / "data"


def keys_file() -> Path:
    raw = os.environ.get("TTS_POOL_KEYS_FILE")
    return Path(raw) if raw else home() / ".gemini_tts_keys"


def state_file() -> Path:
    return data_dir() / "tts_pool_state.json"


def usage_file() -> Path:
    return data_dir() / "tts_pool_usage.jsonl"


def budget_file() -> Path:
    return data_dir() / "tts_pool_budget.json"


def settings_file(name: str) -> Path:
    """A one-line settings file, e.g. voice.txt / style.txt / tempo.txt / max_rate.txt."""
    return data_dir() / f"tts_pool_{name}.txt"


def _read_line(name: str, default: str = "") -> str:
    try:
        value = settings_file(name).read_text(encoding="utf-8").strip()
    except OSError:
        return default
    return value or default


def _read_float(name: str, default: float, low: float, high: float) -> float:
    try:
        value = float(_read_line(name))
    except ValueError:
        return default
    return min(high, max(low, value))


def default_voice() -> str:
    return os.environ.get("TTS_POOL_VOICE") or _read_line("voice", DEFAULT_VOICE)


def style() -> str:
    """Delivery notes. They travel as a separate field and are never spoken aloud."""
    return _read_line("style")


def tempo() -> float:
    """atempo multiplier: 0.94 means 6% slower. Bounds are what atempo accepts."""
    return _read_float("tempo", DEFAULT_TEMPO, 0.5, 2.0)


def pace_ceiling() -> float:
    """Characters per second of speech we allow. 0 disables the ceiling."""
    value = _read_float("max_rate", DEFAULT_PACE_CEILING, 0.0, 100.0)
    return 0.0 if value <= 0 else value


def model_chain() -> list[tuple[str, float]]:
    """Main model first, then fallbacks. A one-line file makes a rollback trivial."""
    lines = [
        line.strip()
        for line in _read_line("model").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    known = dict(DEFAULT_MODELS)
    chain = [(name, known.get(name, 10.0)) for name in lines]
    return chain or list(DEFAULT_MODELS)


def budget_cap_usd() -> float:
    """Hard monthly ceiling. 0 means no ceiling."""
    try:
        payload = json.loads(budget_file().read_text(encoding="utf-8"))
    except Exception:
        return 0.0
    try:
        return float(payload.get("cap_usd") or 0)
    except (TypeError, ValueError):
        return 0.0
