from __future__ import annotations

import pytest

from tts_voice_pool import cli, ledger, speech
from tts_voice_pool import pool as pool_mod


def test_the_modes_are_mutually_exclusive():
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["--check", "--usage"])


def test_usage_prints_a_report(capsys):
    assert cli.main(["--usage"]) == 0
    assert "month" in capsys.readouterr().out


def test_an_empty_pool_is_a_clean_exit_code(pool_home, capsys):
    assert cli.main(["--say", "hi", "out.ogg"]) == 2
    assert "key pool is empty" in capsys.readouterr().out


def test_the_probe_sentence_can_be_inspected(capsys):
    assert cli.main(["--probe-text"]) == 0
    assert "technical check" in capsys.readouterr().out


def test_an_empty_text_file_is_a_usage_error(keys_file, tmp_path):
    source = tmp_path / "empty.txt"
    source.write_text("   \n", encoding="utf-8")
    assert cli.main(["--say-file", str(source), str(tmp_path / "o.ogg")]) == 1


def test_silence_is_reported_not_hidden(keys_file, tmp_path, monkeypatch):
    monkeypatch.setattr(speech, "speak", lambda *a, **k: speech.SynthResult(False, "http 500: no"))
    monkeypatch.setattr(speech, "edge", lambda text, out: speech.SynthResult(False, "edge broken"))
    pool = pool_mod.Pool.load()
    assert cli.say(pool, "hi", tmp_path / "o.ogg", "Kore", "", None, 10.0) == 2


def test_the_working_key_is_remembered_and_the_call_is_logged(keys_file, tmp_path, monkeypatch):
    def fake_speak(key, text, out_path, voice, style, model, rate):
        return speech.SynthResult(
            True, "ok", seconds=3.0, audio_tokens=75, text_tokens=10, voice=voice, model=model
        )

    monkeypatch.setattr(speech, "speak", fake_speak)
    pool = pool_mod.Pool.load()

    assert cli.say(pool, "hello", tmp_path / "o.ogg", "Kore", "", None, 10.0) == 0

    rows = ledger.rows()
    assert len(rows) == 1
    assert rows[0]["key"] == "first" and rows[0]["seconds"] == 3.0
    assert pool.for_key("first").last_ok > 0
    assert pool.for_key("first").fails == 0


def test_a_quota_error_rests_the_key_and_the_next_key_speaks(keys_file, tmp_path, monkeypatch):
    answers = {
        "KEY-ONE": speech.SynthResult(False, "quota"),
        "KEY-TWO": speech.SynthResult(True, "ok", seconds=1.0),
    }
    monkeypatch.setattr(speech, "speak", lambda key, *a, **k: answers[key])
    pool = pool_mod.Pool.load()

    assert cli.say(pool, "hello", tmp_path / "o.ogg", "Kore", "", None, 10.0) == 0

    assert pool.for_key("first").cooldown_until > 0
    assert pool.for_key("first").resting(now=0.0) is True
    assert ledger.rows()[0]["key"] == "second"


def test_a_spent_budget_does_not_call_a_paid_provider(keys_file, tmp_path, monkeypatch):
    monkeypatch.setattr(ledger, "over_budget", lambda *a, **k: True)
    monkeypatch.setattr(speech, "speak", lambda *a, **k: pytest.fail("a paid call was made"))
    monkeypatch.setattr(
        speech,
        "edge",
        lambda text, out: speech.SynthResult(
            True, "ok", seconds=2.0, voice="Svetlana", model="edge"
        ),
    )
    pool = pool_mod.Pool.load()

    assert cli.say(pool, "hello", tmp_path / "o.ogg", "Kore", "", None, 10.0) == 0
    assert ledger.rows()[0]["key"] == "edge"
