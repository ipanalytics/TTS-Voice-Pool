# TTS Voice Pool

Speak through several TTS providers from one call. Keys rotate, voices fall back, the pace
stays listenable, and the bill is visible.

This is the voice layer of an assistant that has to speak on a schedule. A scheduled job has
nobody to ask when the voice fails, so every failure had to become a decision the code can
make on its own — and those decisions are what this repository is really about.

```sh
tts-voice-pool --say "Good morning. Three things on today's list." briefing.ogg
# voiced by gemini-2.5-flash-preview-tts with key main -> briefing.ogg (6.4s)
```

## The problems it solves

**1. One key is a single point of failure.** A quota error (429) and a revoked key (401/403)
need different answers: one key should rest and let another speak, the other is out until a
human replaces it. The pool keeps per-key state (`last_ok`, `cooldown_until`, `dead_reason`)
in one JSON file, tries the key that worked most recently first, and rests a quota-limited key
for a few minutes instead of either hammering it or writing it off for half an hour.

**2. A designed voice can expire.** When the expensive voice is gone, the episode still has to
be voiced, so the pool falls back to a prebuilt voice — but only when the failure was that
voice's fault. A quota error is the key's fault and is *not* wrapped in a second attempt.

**3. "Characters per second" over the whole file lies.** A long episode can average 12.3
chars/s across the text and still be read at 16–17 inside the sentences, and that is what a
listener calls "too fast". The pool measures the *speech* (`ffmpeg silencedetect`), and if the
articulation rate is above the ceiling it stretches the take with `atempo` — never below 0.72,
because past that the artefact is audible. The delivered file's duration is what gets logged,
not the one from before the stretch.

**4. A free quota that quietly starts billing looks exactly like one that did not.** Every
synthesis appends a row (seconds, tokens, what it would have cost), `--usage` turns the rows
into minutes and dollars, and a hard monthly cap sends the next call to the free engine
instead of the paid one.

## Install

```sh
git clone https://github.com/ipanalytics/TTS-Voice-Pool
cd TTS-Voice-Pool
python -m pip install -e ".[edge]"     # edge-tts is the optional free fallback
```

`ffmpeg` and `ffprobe` must be on `PATH`: they measure the pace and encode the container.

Keys, one per line — a label, `=`, the key. A bare line is its own label, `#` is a comment:

```text
main=AIza...
backup=AIza...
```

Default location `$TTS_POOL_HOME/.gemini_tts_keys` (or `$HERMES_HOME/...`), or point
`TTS_POOL_KEYS_FILE` anywhere, or pass them in `GEMINI_API_KEYS="main=...,backup=..."` — which
is what makes it usable in CI without mounting a secret. Keys are read at call time and never
written to the state file.

## Command line

```sh
tts-voice-pool --check                       # probe every key, print a table
tts-voice-pool --say "text" out.ogg          # synthesise, print which key answered
tts-voice-pool --say-file text.txt out.mp3   # read the text from a file
tts-voice-pool --usage                       # this month: syntheses, minutes, notional cost
tts-voice-pool --voice voice_abc123 --style "read this calmly" --say "text" out.ogg
```

Exit codes are part of the interface, because a scheduled job reads nothing else:
`0` spoken, `2` nothing could speak (no key and the free fallback failed too), `1` bad usage.

## Library

```python
from tts_voice_pool import say

code = say("Good morning.", "briefing.ogg")            # 0 spoken, 2 silent
code = say("Text", "out.mp3", voice="voice_abc123", style="warm, unhurried")
```

Provider failures come back as a return code, never as an exception: the caller is usually a
cron job that would rather send plain text than crash.

## Configuration

| Setting | Environment | File (in `$TTS_POOL_HOME/data`) | Default |
| --- | --- | --- | --- |
| Base directory | `TTS_POOL_HOME`, then `HERMES_HOME` | — | `~/.hermes` |
| Key pool | `GEMINI_API_KEYS`, `TTS_POOL_KEYS_FILE` | `.gemini_tts_keys` | empty |
| Default voice | `TTS_POOL_VOICE` | `tts_pool_voice.txt` | `Kore` |
| Delivery notes | — | `tts_pool_style.txt` | empty |
| Tempo multiplier | — | `tts_pool_tempo.txt` | `1.0` (clamped 0.5–2.0) |
| Pace ceiling, chars/s | — | `tts_pool_max_rate.txt` | `12.3` (0 disables) |
| Model chain | — | `tts_pool_model.txt` | `gemini-2.5-flash-preview-tts` |
| Monthly cap, USD | — | `tts_pool_budget.json` (`{"cap_usd": 5}`) | `0` (no cap) |
| Free fallback voice | `TTS_POOL_EDGE_VOICE` | — | `ru-RU-SvetlanaNeural` |

Delivery notes are never glued into the text by default. A measured example from this project:
74 characters of transcript plus notes came back as ~200 characters of speech, because the
model read the notes aloud — they travel as a separate field, and `TTS_POOL_PREPEND_STYLE=1`
restores the old behaviour for anyone who needs it.

## Testing

```sh
python -m pytest -q      # 43 passed
python -m ruff check .   # clean
```

No network and no audio hardware in the tests: HTTP is stubbed, the pace maths is checked
against known numbers, and every test gets its own `$TTS_POOL_HOME`, so a test run can never
touch a real ledger or key file. The parts that need a live API (does a key still work, does
the model answer) are exactly the parts `--check` exists for.

## What this is not

Not a streaming synthesiser, not a local model runner, and not a voice cloner. It is the small
layer between "I have several keys and a text" and "a playable file came out", with the failure
paths written down.

## License

MIT.
