"""Key rotation with memory: which key works, which is resting, which is dead.

One API key that hits its quota takes the whole voice channel down with it, and a scheduled
job has nobody to ask. So the pool keeps a small state file per key:

* ``last_ok`` — used to try the key that worked most recently first;
* ``cooldown_until`` — after a 429 the key rests for a few minutes instead of being hammered
  (and instead of being written off for half an hour);
* ``dead_reason`` — a 401/403 is not a quota: the key is out until a human replaces it.

State lives in one JSON file. It is deliberately not a database: losing it costs one extra
failed request, not a migration.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from . import config

QUOTA = "quota"
DEAD = "dead"
FAILED = "error"


@dataclass
class KeyState:
    last_ok: float = 0.0
    last_check: float = 0.0
    alive: bool | None = None
    reason: str = ""
    model: str = ""
    cooldown_until: float = 0.0
    dead_reason: str = ""
    last_error: str = ""
    fails: int = 0

    def resting(self, now: float) -> bool:
        return bool(self.dead_reason) or now < self.cooldown_until

    def as_dict(self) -> dict:
        return {
            key: value
            for key, value in self.__dict__.items()
            if value not in ("", 0, 0.0, None, False)
        }

    @classmethod
    def from_dict(cls, payload: dict) -> KeyState:
        known = {key: value for key, value in (payload or {}).items() if key in cls().__dict__}
        return cls(**known)


@dataclass
class Pool:
    """Keys plus their state, and the order in which to try them."""

    keys: list[tuple[str, str]] = field(default_factory=list)
    state: dict[str, KeyState] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return bool(self.keys)

    def __len__(self) -> int:
        return len(self.keys)

    def for_key(self, label: str) -> KeyState:
        return self.state.setdefault(label, KeyState())

    def order(self, now: float) -> list[tuple[str, str]]:
        """Most recently successful key first; resting keys go last, not away.

        They are still returned: if every key is resting the caller should try anyway rather
        than refuse to speak — a cooldown is a hint, not a promise.
        """
        return sorted(
            self.keys,
            key=lambda item: (
                self.state.get(item[0], KeyState()).resting(now),
                -self.state.get(item[0], KeyState()).last_ok,
            ),
        )

    def available(self, now: float) -> list[tuple[str, str]]:
        return [item for item in self.order(now) if not self.for_key(item[0]).resting(now)]

    def mark_ok(self, label: str, now: float) -> None:
        state = self.for_key(label)
        state.last_ok = now
        state.alive = True
        state.reason = "ok"
        state.fails = 0
        state.cooldown_until = 0.0
        state.dead_reason = ""

    def mark_failed(self, label: str, reason: str, now: float) -> None:
        state = self.for_key(label)
        state.alive = False
        state.reason = reason
        state.fails += 1
        if reason == QUOTA:
            state.cooldown_until = now + config.COOLDOWN_SECONDS
        elif reason == DEAD:
            state.dead_reason = reason
        else:
            state.last_error = reason

    def save(self, path=None) -> None:
        target = path or config.state_file()
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {label: self.for_key(label).as_dict() for label, _key in self.keys}
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(target)

    @classmethod
    def load(cls, path=None) -> Pool:
        pool = cls(keys=parse_keys())
        target = path or config.state_file()
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
        except Exception:
            payload = {}
        for label, _key in pool.keys:
            pool.state[label] = KeyState.from_dict(payload.get(label) or {})
        return pool


def parse_keys(text: str | None = None, path=None) -> list[tuple[str, str]]:
    """Read the key pool.

    Format is one key per line, ``label=KEY`` (a bare line is both label and key), ``#``
    starts a comment. A ``GEMINI_API_KEYS`` environment variable wins over the file, which
    is what makes the package usable in CI without mounting a secret file.
    """
    env = os.environ.get("GEMINI_API_KEYS")
    if env:
        source_text = env.replace(";", "\n")
    else:
        target = path or config.keys_file()
        try:
            source_text = target.read_text(encoding="utf-8")
        except OSError:
            return []
    out: list[tuple[str, str]] = []
    for raw in (source_text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        label, _, key = line.partition("=")
        label = (label or f"key{len(out) + 1}").strip()
        key = key.strip() if key else label
        if key:
            out.append((label, key))
    return out
