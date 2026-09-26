"""The command line: probe the pool, say something, look at the bill.

    tts-voice-pool --check                     probe every key, print a table
    tts-voice-pool --say "text" out.ogg        synthesise, printing which key answered
    tts-voice-pool --say-file text.txt out.ogg read the text from a file
    tts-voice-pool --usage                     this month's minutes and notional cost
    tts-voice-pool --install-service           print the systemd --user unit for a voice job

Exit codes are part of the interface, because a scheduled job has nothing else to read:
``0`` spoken, ``2`` no key or the free fallback also failed, ``1`` bad usage.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from . import config, ledger, speech
from . import pool as pool_mod

PROBE_TEXT = "This is a short technical check of the speech channel."
SERVICE_TEMPLATE = """[Unit]
Description=TTS voice pool check

[Service]
Type=oneshot
Environment=TTS_POOL_HOME={home}
ExecStart={python} -m tts_voice_pool --check

[Install]
WantedBy=default.target
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tts-voice-pool",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true", help="probe all keys and print a table")
    group.add_argument("--say", nargs=2, metavar=("TEXT", "OUT"), help="synthesise TEXT into OUT")
    group.add_argument("--say-file", nargs=2, metavar=("FILE", "OUT"), help="read TEXT from FILE")
    group.add_argument("--usage", action="store_true", help="monthly usage and notional cost")
    group.add_argument(
        "--probe-text", action="store_true", help="print the probe sentence and exit"
    )
    parser.add_argument("--voice", help="voice name or designed voice id (voice_...)")
    parser.add_argument("--style", help="delivery notes for this call only")
    parser.add_argument("--model", help="force one model instead of the configured chain")
    parser.add_argument(
        "--rate", type=float, default=10.0, help="USD per 1M audio tokens for costing"
    )
    return parser


def say(
    pool: pool_mod.Pool,
    text: str,
    out: Path,
    voice: str,
    style: str,
    model: str | None,
    rate: float,
) -> int:
    now = time.time()
    if ledger.over_budget():
        print(f"monthly budget is spent (${ledger.month_spend():.2f}) — not calling Google")
        return free_fallback(text, out)
    chain = [(model, rate)] if model else config.model_chain()
    for label, key in pool.order(now):
        for index, (model_name, model_rate) in enumerate(chain):
            result = speech.speak(key, text, out, voice, style, model_name, model_rate)
            for note in result.notes:
                print(f"  {label}: {note}")
            if result.ok:
                pool.mark_ok(label, now)
                pool.save()
                ledger.log_usage(
                    label,
                    result.voice,
                    result.model,
                    len(text),
                    result.seconds,
                    result.audio_tokens,
                    result.text_tokens,
                    model_rate,
                )
                print(f"voiced by {result.model} with key {label} -> {out} ({result.seconds:.1f}s)")
                return 0
            if result.reason in (pool_mod.QUOTA, pool_mod.DEAD):
                pool.mark_failed(label, result.reason, now)
                pool.save()
                break  # this key is out regardless of the model
            if index + 1 < len(chain):
                print(f"{label}/{model_name}: {result.reason} — trying the next model")
            else:
                pool.mark_failed(label, result.reason, now)
                pool.save()
                print(f"{label}: {result.reason} — next key")
    print("no key answered — falling back to the free engine")
    return free_fallback(text, out)


def free_fallback(text: str, out: Path) -> int:
    result = speech.edge(text, out)
    if result.ok:
        print(f"voiced by {result.voice} (edge) -> {out}")
        ledger.log_usage(
            "edge",
            result.voice,
            "edge",
            len(text),
            result.seconds,
            result.audio_tokens,
            result.text_tokens,
            0.0,
        )
        return 0
    print(result.reason)
    return 2


def check(pool: pool_mod.Pool, voice: str, rate: float) -> int:
    """Probe each key against the model chain: a live key must not be written off because
    one model is having a bad day."""
    now = time.time()
    out = Path(config.home()) / "audio_cache" / "pool_probe.ogg"
    for label, key in pool.keys:
        alive, reason, used = False, "", ""
        for model_name, model_rate in config.model_chain():
            result = speech.speak(key, PROBE_TEXT, out, voice, "", model_name, rate or model_rate)
            alive, reason, used = result.ok, result.reason, model_name
            if result.ok or reason in (pool_mod.QUOTA, pool_mod.DEAD):
                break
            print(f"{label}: {model_name} failed ({reason}) — trying the next model")
        state = pool.for_key(label)
        state.last_check = now
        state.alive = alive
        state.reason = reason
        state.model = used
        print(f"{label}: {'alive' if alive else 'dead — ' + reason}")
    pool.save()
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.probe_text:
        print(PROBE_TEXT)
        return 0
    if args.usage:
        print(ledger.report())
        return 0
    pool = pool_mod.Pool.load()
    if not pool:
        print(f"the key pool is empty: {config.keys_file()} (or GEMINI_API_KEYS)")
        return 2
    voice = args.voice or config.default_voice()
    if args.check:
        return check(pool, voice, args.rate)
    text, out = args.say or (
        Path(args.say_file[0]).read_text(encoding="utf-8").strip(),
        args.say_file[1],
    )
    if not text.strip():
        print("nothing to say: the text is empty")
        return 1
    return say(pool, text, Path(out), voice, args.style or config.style(), args.model, args.rate)


if __name__ == "__main__":
    sys.exit(main())
