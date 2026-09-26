from __future__ import annotations

import json

from tts_voice_pool import config, ledger


def _row(**over):
    payload = {
        "label": "first",
        "voice": "Kore",
        "model": "m",
        "chars": 100,
        "seconds": 5.0,
        "audio_tokens": 600_000,
        "text_tokens": 1000,
        "rate": 10.0,
    }
    payload.update(over)
    return ledger.log_usage(
        payload["label"],
        payload["voice"],
        payload["model"],
        payload["chars"],
        payload["seconds"],
        payload["audio_tokens"],
        payload["text_tokens"],
        payload["rate"],
    )


def test_a_row_records_what_the_call_would_have_cost():
    row = _row()
    assert row["usd_if_paid"] == 6.0005  # 0.6M audio tokens @ $10/1M + 1k text @ $0.5/1M
    assert row["seconds"] == 5.0 and row["chars"] == 100


def test_rows_are_filtered_by_month():
    _row()
    assert len(ledger.rows()) == 1
    assert ledger.rows("1999-01") == []


def test_unreadable_lines_are_skipped():
    _row()
    with ledger.config.usage_file().open("a", encoding="utf-8") as handle:
        handle.write("{broken\n")
    assert len(ledger.rows()) == 1


def test_the_report_sums_minutes_and_keys():
    _row()
    _row(label="second", seconds=10.0)
    text = ledger.report()
    assert "2 syntheses" in text
    assert "0.2 min" in text  # 15 seconds of speech
    assert "key first: 1" in text and "key second: 1" in text


def test_the_budget_is_a_hard_ceiling(pool_home):
    assert ledger.over_budget() is False  # no cap configured means no ceiling
    config.budget_file().parent.mkdir(parents=True, exist_ok=True)
    config.budget_file().write_text(json.dumps({"cap_usd": 1.0}), encoding="utf-8")
    assert ledger.over_budget() is False
    _row()
    assert ledger.over_budget() is True
    assert "$1.00" in ledger.report()


def test_a_broken_budget_file_means_no_ceiling(pool_home):
    config.budget_file().parent.mkdir(parents=True, exist_ok=True)
    config.budget_file().write_text("{}", encoding="utf-8")
    assert config.budget_cap_usd() == 0.0
