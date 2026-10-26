"""EvidenceRecord: one typed claim about one subject, with its certainty level.

The record id is the content hash of every other field, so a record cannot be
altered without changing its identity (and breaking the bundle chain it sits in).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from worldline.core import EvidenceKind, content_hash

KERNEL_PRODUCER = "kernel"
AGENT_PREFIX = "agent:"

# Certainty ordering used by Policy.min_kind. FAILED is ranked for comparison
# purposes only; it never satisfies any requirement (see policy.kind_satisfies).
CERTAINTY_RANK: dict[EvidenceKind, int] = {
    EvidenceKind.UNKNOWN: 0,
    EvidenceKind.FAILED: 1,
    EvidenceKind.EMPIRICALLY_VALIDATED: 2,
    EvidenceKind.ATTESTED: 3,
    EvidenceKind.PROVED_WITHIN_MODEL: 4,
}

# Kinds only the kernel may emit. Agents report observations; the kernel
# re-derives them and is the sole source of ATTESTED / PROVED_WITHIN_MODEL.
KERNEL_ONLY_KINDS: frozenset[EvidenceKind] = frozenset(
    {EvidenceKind.ATTESTED, EvidenceKind.PROVED_WITHIN_MODEL}
)


def utc_now_iso() -> str:
    """Wall time is allowed here only as record metadata (derived_at)."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso(ts: str) -> datetime:
    s = ts[:-1] + "+00:00" if ts.endswith("Z") else ts
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def is_agent_producer(producer: str) -> bool:
    return producer.startswith(AGENT_PREFIX)


@dataclass(frozen=True)
class EvidenceRecord:
    kind: EvidenceKind
    claim: str
    subject: str
    procedure: str
    inputs_hash: str
    result: dict = field(default_factory=dict)
    assumptions: list[str] = field(default_factory=list)
    derived_at: str = field(default_factory=utc_now_iso)
    producer: str = KERNEL_PRODUCER
    id: str = ""  # filled from content; passing a wrong id raises

    def __post_init__(self) -> None:
        if not isinstance(self.kind, EvidenceKind):
            object.__setattr__(self, "kind", EvidenceKind(self.kind))
        object.__setattr__(self, "assumptions", list(self.assumptions))
        if not (self.producer == KERNEL_PRODUCER or is_agent_producer(self.producer)):
            raise ValueError(
                f"producer must be 'kernel' or 'agent:<name>', got {self.producer!r}"
            )
        expected = content_hash(self.body())
        if self.id and self.id != expected:
            raise ValueError(f"EvidenceRecord id {self.id} does not match content {expected}")
        object.__setattr__(self, "id", expected)

    def body(self) -> dict:
        """Every field except id, in canonical form."""
        return {
            "kind": self.kind.value,
            "claim": self.claim,
            "subject": self.subject,
            "procedure": self.procedure,
            "inputs_hash": self.inputs_hash,
            "result": self.result,
            "assumptions": list(self.assumptions),
            "derived_at": self.derived_at,
            "producer": self.producer,
        }

    def to_dict(self) -> dict:
        d = self.body()
        d["id"] = self.id
        return d

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "EvidenceRecord":
        d = dict(d)
        d["kind"] = EvidenceKind(d["kind"])
        return EvidenceRecord(**d)

    def validate_producer(self) -> None:
        """Agents may not self-certify: only the kernel emits kernel-only kinds."""
        if self.kind in KERNEL_ONLY_KINDS and self.producer != KERNEL_PRODUCER:
            raise ValueError(
                f"producer {self.producer!r} may not emit {self.kind.value}; "
                f"only 'kernel' can. Agents emit EMPIRICALLY_VALIDATED, FAILED or UNKNOWN "
                f"and the kernel re-derives them."
            )
