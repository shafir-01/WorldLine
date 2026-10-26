"""Event v1: the one record type every WORLDLINE component speaks.

Four notions of time are never merged:
  event_time_ns      when the thing happened in the simulated world (truth clock)
  received_time_ns   when a given observer actually received it (None = not yet / never)
  derived_at_ns      when an evaluator produced a conclusion from it (lives on Verdict records)
  simulated_truth_at reserved for truth-store records only
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

SCHEMA_VERSION = "event.v1"


class Verdict(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"


class EvidenceKind(str, Enum):
    """Certainty levels. Never present one as another."""

    PROVED_WITHIN_MODEL = "PROVED_WITHIN_MODEL"
    EMPIRICALLY_VALIDATED = "EMPIRICALLY_VALIDATED"
    ATTESTED = "ATTESTED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


def _default(o: Any):
    if isinstance(o, Enum):
        return o.value
    if hasattr(o, "__dataclass_fields__"):
        return asdict(o)
    if isinstance(o, (set, frozenset, tuple)):
        return sorted(o) if isinstance(o, (set, frozenset)) else list(o)
    raise TypeError(f"not serializable: {type(o)}")


def canonical_json(obj: Any) -> str:
    """Deterministic serialization: sorted keys, no whitespace, UTF-8, enums as values."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=_default)


def content_hash(obj: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Event:
    id: str
    scenario_id: str
    source_id: str
    event_time_ns: int
    kind: str
    payload: dict = field(default_factory=dict)
    received_time_ns: int | None = None
    geometry: dict | None = None
    unit: str | None = None
    quality: float | None = None
    policy_labels: tuple[str, ...] = ()
    provenance_ids: tuple[str, ...] = ()
    schema_version: str = SCHEMA_VERSION
    priority: int = 0

    def body(self) -> dict:
        d = asdict(self)
        d["policy_labels"] = list(self.policy_labels)
        d["provenance_ids"] = list(self.provenance_ids)
        return d

    @property
    def content_hash(self) -> str:
        return content_hash(self.body())

    def sort_key(self) -> tuple:
        """Canonical total order inside one world: (time, priority desc, id)."""
        return (self.event_time_ns, -self.priority, self.id)

    def replace(self, **changes: Any) -> "Event":
        d = asdict(self)
        d.update(changes)
        return Event.from_dict(d)

    def with_receipt(self, received_time_ns: int) -> "Event":
        return self.replace(received_time_ns=received_time_ns)

    @staticmethod
    def from_dict(d: dict) -> "Event":
        d = dict(d)
        d["policy_labels"] = tuple(d.get("policy_labels", ()) or ())
        d["provenance_ids"] = tuple(d.get("provenance_ids", ()) or ())
        return Event(**d)
