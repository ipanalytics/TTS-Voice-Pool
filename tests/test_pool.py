from __future__ import annotations

from tts_voice_pool import config
from tts_voice_pool.pool import DEAD, QUOTA, KeyState, Pool, parse_keys


def test_keys_are_read_from_a_file(keys_file):
    assert parse_keys() == [("first", "KEY-ONE"), ("second", "KEY-TWO")]


def test_comments_and_bare_lines(keys_file):
    keys_file.write_text("# a comment\n\nonlykey\nx=KX\n", encoding="utf-8")
    assert parse_keys() == [("onlykey", "onlykey"), ("x", "KX")]


def test_the_environment_wins_over_the_file(keys_file, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEYS", "a=A;B=B")
    assert parse_keys() == [("a", "A"), ("B", "B")]


def test_a_missing_file_is_not_an_error(pool_home):
    assert parse_keys() == []
    assert not Pool.load()


def test_the_key_that_worked_last_is_tried_first(keys_file):
    pool = Pool.load()
    pool.for_key("second").last_ok = 500.0
    pool.for_key("first").last_ok = 100.0
    assert [label for label, _key in pool.order(1000.0)] == ["second", "first"]


def test_a_resting_key_goes_last_but_is_not_forgotten(keys_file):
    pool = Pool.load()
    pool.mark_failed("first", QUOTA, now=1000.0)
    order = [label for label, _key in pool.order(1001.0)]
    assert order == ["second", "first"]
    assert config.COOLDOWN_SECONDS >= 60  # a rest, not a life sentence
    assert [label for label, _key in pool.available(1001.0)] == ["second"]


def test_a_dead_key_stays_out(keys_file):
    pool = Pool.load()
    pool.mark_failed("first", DEAD, now=1000.0)
    assert pool.for_key("first").dead_reason == DEAD
    assert pool.for_key("first").resting(now=10_000_000.0)
    assert [label for label, _key in pool.available(10_000_000.0)] == ["second"]


def test_success_clears_the_cooldown(keys_file):
    pool = Pool.load()
    pool.mark_failed("first", QUOTA, now=1000.0)
    pool.mark_ok("first", now=1100.0)
    state = pool.for_key("first")
    assert state.cooldown_until == 0.0 and state.fails == 0 and not state.resting(1100.0)


def test_state_survives_a_round_trip(keys_file, pool_home):
    pool = Pool.load()
    pool.mark_failed("first", DEAD, now=1000.0)
    pool.for_key("second").reason = "quota"
    pool.save()
    again = Pool.load()
    assert again.for_key("first").dead_reason == DEAD
    assert again.for_key("second").reason == "quota"


def test_unknown_state_fields_are_ignored():
    state = KeyState.from_dict({"last_ok": 5.0, "invented": True})
    assert state.last_ok == 5.0 and not hasattr(state, "invented")


def test_a_corrupt_state_file_does_not_break_the_pool(keys_file, pool_home):
    config.state_file().parent.mkdir(parents=True, exist_ok=True)
    config.state_file().write_text("{not json", encoding="utf-8")
    pool = Pool.load()
    assert len(pool) == 2 and pool.for_key("first").last_ok == 0.0
