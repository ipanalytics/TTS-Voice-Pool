"""What every call would have cost in the paid tier, and what the month has added up to.

The pool exists to stay inside a free or cheap quota, but "cheap" still has to be visible:
each successful synthesis appends one line, and the monthly report turns those lines into
minutes of speech and dollars. Without it, a quota that quietly started billing looks
exactly like one that did not.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime

from . import config

# Text tokens are billed separately; audio dominates the cost but not by infinity.
TEXT_USD_PER_MTOKEN = 0.5
AUDIO_TOKENS_PER_SECOND = 25.0


def log_usage(
    label: str,
    voice: str | None,
    model: str | None,
    chars: int,
    seconds: float,
    audio_tokens: int,
    text_tokens: int,
    rate_usd_per_mtoken: float,
) -> dict:
    """Append one row for a successful synthesis and return it."""
    usd = (audio_tokens / 1e6) * float(rate_usd_per_mtoken) + (
        text_tokens / 1e6
    ) * TEXT_USD_PER_MTOKEN
    row = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "key": label,
        "voice": voice,
        "model": model,
        "chars": int(chars),
        "seconds": round(float(seconds or 0), 2),
        "audio_tokens": int(audio_tokens),
        "text_tokens": int(text_tokens),
        "usd_if_paid": round(usd, 5),
    }
    path = config.usage_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def rows(month: str | None = None, path=None) -> list[dict]:
    """Rows of one month (YYYY-MM, default: the current one). Unreadable lines are skipped."""
    month = month or datetime.now(UTC).strftime("%Y-%m")
    source = path or config.usage_file()
    if not source.exists():
        return []
    out: list[dict] = []
    for line in source.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if str(row.get("ts", ""))[:7] == month:
            out.append(row)
    return out


def month_spend(month: str | None = None) -> float:
    return sum(float(row.get("usd_if_paid") or 0) for row in rows(month))


def over_budget(month: str | None = None) -> bool:
    cap = config.budget_cap_usd()
    return bool(cap) and month_spend(month) >= cap


def report(month: str | None = None) -> str:
    """Human-readable summary: how often, how long, and what it would have cost."""
    month = month or datetime.now(UTC).strftime("%Y-%m")
    data = rows(month)
    by_key: dict[str, int] = {}
    by_model: dict[str, int] = {}
    seconds = 0.0
    for row in data:
        by_key[row["key"]] = by_key.get(row["key"], 0) + 1
        name = row.get("model") or "—"
        by_model[name] = by_model.get(name, 0) + 1
        seconds += float(row.get("seconds") or 0)
    lines = [
        f"month {month}: {len(data)} syntheses, {seconds / 60:.1f} min of speech, "
        f"${month_spend(month):.2f} if it were a paid tier"
    ]
    for key, count in sorted(by_key.items(), key=lambda item: -item[1]):
        lines.append(f"  key {key}: {count}")
    for name, count in sorted(by_model.items(), key=lambda item: -item[1]):
        lines.append(f"  model {name}: {count}")
    cap = config.budget_cap_usd()
    if cap:
        lines.append(f"  budget: ${month_spend(month):.2f} of ${cap:.2f}")
    return "\n".join(lines)
