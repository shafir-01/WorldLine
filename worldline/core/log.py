"""Append-only event log with a running hash chain and canonical ordering."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Iterable, Iterator

from .event import Event

GENESIS = "sha256:" + "0" * 64


def _link(prev: str, h: str) -> str:
    return "sha256:" + hashlib.sha256((prev + h).encode()).hexdigest()


@dataclass
class EventLog:
    events: list[Event] = field(default_factory=list)
    chain: list[str] = field(default_factory=list)  # chain[i] = H(chain[i-1] || events[i].content_hash)

    def append(self, ev: Event) -> str:
        prev = self.chain[-1] if self.chain else GENESIS
        h = _link(prev, ev.content_hash)
        self.events.append(ev)
        self.chain.append(h)
        return h

    def extend(self, evs: Iterable[Event]) -> None:
        for e in sorted(evs, key=Event.sort_key):
            self.append(e)

    @property
    def digest(self) -> str:
        return self.chain[-1] if self.chain else GENESIS

    def __len__(self) -> int:
        return len(self.events)

    def __iter__(self) -> Iterator[Event]:
        return iter(self.events)

    def ordered(self) -> list[Event]:
        return sorted(self.events, key=Event.sort_key)

    def to_dict(self) -> dict:
        return {"events": [e.body() for e in self.events], "digest": self.digest}

    @staticmethod
    def from_dict(d: dict) -> "EventLog":
        log = EventLog()
        for e in d["events"]:
            log.append(Event.from_dict(e))
        if d.get("digest") and d["digest"] != log.digest:
            raise ValueError("event log digest mismatch: log was altered")
        return log

    def verify(self) -> list[int]:
        """Return indices where the chain is broken (empty list = intact)."""
        bad: list[int] = []
        prev = GENESIS
        for i, (e, h) in enumerate(zip(self.events, self.chain)):
            if _link(prev, e.content_hash) != h:
                bad.append(i)
            prev = h
        return bad
