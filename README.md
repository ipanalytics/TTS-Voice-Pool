# TTS Voice Pool

_Русская версия: [README.ru.md](README.ru.md)_

**Speak through several TTS providers from one call.** Keys rotate when one is rate-limited,
a designed voice falls back to a prebuilt one when it expires, the pace is capped at a rate a
human can actually listen to, and every synthesis is priced in a ledger so a free tier that
started billing cannot stay invisible.

![license](https://img.shields.io/badge/license-MIT-blue)
![python](https://img.shields.io/badge/python-3.11%2B-blue)
![tests](https://img.shields.io/badge/tests-43%20passed-brightgreen)
![dependencies](https://img.shields.io/badge/runtime%20deps-none-lightgrey)

```sh
tts-voice-pool --say "Good morning. Three things on today's list." briefing.ogg
# voiced by gemini-2.5-flash-preview-tts with key main -> briefing.ogg (6.4s)
```

---

## Why this exists

This is the voice layer of an assistant that speaks on a schedule — a daily briefing, an alert,
a reminder. A scheduled job has nobody to ask when the voice fails, so every failure that used
to need a human decision had to become a decision the code makes on its own:

* a key that hits its quota at 03:10, with nobody awake to replace it;
* a designed voice that quietly expired, so the episode either loses its voice or gets a
  different one — and the listener notices the second option more than the code does;
* a take that is technically fine and still unpleasant, because 12 characters per second
  averaged over a long text can mean 17 characters per second inside the sentences;
* a free tier that starts billing, which looks exactly like a free tier that does not.

Each of those now has a decision, a test, and a paragraph here explaining what was measured.
The numbers in this README come from that production use, not from a synthetic benchmark.

## What you get

* **`tts-voice-pool` CLI** — `--check`, `--say`, `--say-file`, `--usage`, and exit codes a cron
  job can branch on.
* **A library** — `from tts_voice_pool import say`; provider failures return a code instead of
  raising, because the caller is usually a job that would rather send text than crash.
* **A key pool with memory** — per-key state in one JSON file: who worked last, who is resting
  after a 429, who is dead until a human replaces the key.
* **A model chain** — declared in a one-line file, so rolling back to the previous model is a
  text edit instead of a deployment.
* **A pace model** — articulation measured with `ffmpeg silencedetect`, capped by `atempo`,
  never stretched below the audible floor.
* **A cost ledger and a hard monthly cap** — after the cap the next call goes to the free
  engine, not to the paid API.
* **A free fallback** — `edge-tts` in a subprocess; a broken key costs a different voice, never
  a silent episode.

## Install

```sh
git clone https://github.com/ipanalytics/TTS-Voice-Pool
cd TTS-Voice-Pool
python -m pip install -e ".[edge]"     # edge-tts is the optional free fallback
```

`ffmpeg` and `ffprobe` must be on `PATH`: they measure the pace and encode the container. No
other runtime dependency — the HTTP calls use the standard library.

## The key file

One key per line: a label, `=`, the key. A bare line is both label and key; `#` is a comment.

```text
main=AIza...
backup=AIza...
# laptop=AIza...
```

Read from, in order: `GEMINI_API_KEYS` (`main=...,backup=...`), `TTS_POOL_KEYS_FILE`, then
`$TTS_POOL_HOME/.gemini_tts_keys` (falling back to `$HERMES_HOME`). Keys are read on every call
and never written into the state file, so the two files can have different permissions.

## How a call is made

1. **Budget first.** If the monthly cap is already spent, the call goes straight to the free
   engine. A paid provider is not called to discover that it is not allowed.
2. **Keys in order of recent success.** The key that answered last is tried first, so a healthy
   pool converges on one working key instead of cycling.
3. **Resting keys are skipped, not forgotten.** A key inside its cooldown is moved to the end —
   if every key is resting, the call is still attempted, because a cooldown is a hint.
4. **Models in the configured chain.** A model-level failure moves to the next model; a
   `quota`/`dead` answer stops the loop for that key immediately.
5. **Voice fallback, only where it belongs.** A designed voice that fails with anything other
   than quota/dead is retried once with a prebuilt voice. A quota error is the key's fault and
   is not masked by a second attempt.
6. **Pace, then encode.** The incoming bytes are detected (raw PCM or an already-finished WAV),
   measured, stretched if needed, and encoded to the container the caller asked for.
7. **Ledger.** One JSON line per success: key, voice, model, characters, seconds, tokens, and
   what it would have cost in the paid tier.

## The pace model

Averaging characters over the whole file hides the problem. Measured on this project: a long
episode that looked calm at 12.3 chars/s averaged over the text was being read at 16–17 chars
per second inside the sentences — and that is the part a listener calls "too fast".

So the pool measures speech only:

```text
articulation = characters / (total seconds - detected silence)     # -35 dB, 0.35 s
```

If `articulation` is above the ceiling, the take is stretched with `atempo` by
`ceiling / articulation`, floored at 0.72 (deeper and the stretching becomes audible). The
duration written to the ledger is the duration of the **delivered** file, measured after the
stretch — a report built on the pre-stretch number drifts.

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
| Request timeout, s | `TTS_POOL_TIMEOUT` | — | `90` |

Delivery notes are never glued into the text by default. A measured example from this project:
74 characters of transcript plus notes came back as ~200 characters of speech, because the model
read the notes aloud — so notes travel as a separate field, and `TTS_POOL_PREPEND_STYLE=1`
restores the old behaviour for anyone who wants it.

## Troubleshooting

| Symptom | Usual cause | What the pool does |
| --- | --- | --- |
| `voice pool is empty` and exit code 2 | No key file and no `GEMINI_API_KEYS` | Nothing to try; the caller should fall back to text |
| Silence every night at the same hour | Quota resets while the job runs | The key rests and the next one speaks |
| The voice changed and back again | One key is rate-limited, another is not | Expected: `--usage` shows which key and which model answered |
| Speech sounds rushed | A model returned a fast take | Stretched to the ceiling, min 0.72 |
| Nothing is billed, ever | The free engine answered every call | `--usage` shows the `edge` rows explicitly |
| `ffmpeg failed` | Missing `ffmpeg`/`ffprobe` | The call fails loudly rather than shipping a broken file |

## Testing

```sh
python -m pytest -q      # 43 passed
python -m ruff check .   # clean
```

No network and no audio hardware in the tests: HTTP is stubbed, the pace maths is checked
against known numbers, and every test gets its own `$TTS_POOL_HOME`, so a test run cannot touch
a real ledger or a real key file. What the tests do **not** prove is that a given key still
works — that is exactly what `--check` is for.

## What this is not

Not a streaming synthesiser, not a local model runner, not a voice cloner. It is the small layer
between "I have several keys and a text" and "a playable file came out", with the failure paths
written down — and the free fallback that keeps a scheduled job from going silent.

## Related

* [Hermes-Agent-Ops](https://github.com/ipanalytics/Hermes-Agent-Ops) — running this assistant
  in production: cron hygiene, context budgets, cost governance.
* [Hermes-Plugin-Pack](https://github.com/ipanalytics/Hermes-Plugin-Pack) — the plugins that
  wrap the same assistant.

## License

MIT — see [LICENSE](LICENSE).
