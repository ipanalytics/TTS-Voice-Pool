from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def pool_home(tmp_path, monkeypatch):
    """Every test gets its own TTS_POOL_HOME, so nothing touches a real ledger or key file."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("TTS_POOL_HOME", str(home))
    for name in (
        "GEMINI_API_KEYS",
        "TTS_POOL_VOICE",
        "TTS_POOL_KEYS_FILE",
        "TTS_POOL_PREPEND_STYLE",
        "TTS_POOL_SEPARATE_STYLE",
        "TTS_POOL_EDGE_VOICE",
        "TTS_POOL_TIMEOUT",
    ):
        monkeypatch.delenv(name, raising=False)
    return home


@pytest.fixture
def keys_file(pool_home):
    path = pool_home / ".gemini_tts_keys"
    path.write_text("first=KEY-ONE\nsecond=KEY-TWO\n", encoding="utf-8")
    return path
