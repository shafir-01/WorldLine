"""Policy: what a release needs, loaded from policies/*.yaml."""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from worldline.core import EvidenceKind

from .records import CERTAINTY_RANK, KERNEL_PRODUCER, is_agent_producer

DEFAULT_POLICY_PATH = Path(__file__).resolve().parents[2] / "policies" / "release.yaml"


def kind_satisfies(kind: EvidenceKind, min_kind: EvidenceKind) -> bool:
    """Certainty ordering UNKNOWN < FAILED < EMPIRICALLY_VALIDATED < ATTESTED < PROVED_WITHIN_MODEL.
    FAILED and UNKNOWN never satisfy anything, whatever the floor."""
    if kind in (EvidenceKind.FAILED, EvidenceKind.UNKNOWN):
        return False
    return CERTAINTY_RANK[kind] >= CERTAINTY_RANK[min_kind]


@dataclass(frozen=True)
class Requirement:
    claim_pattern: str                  # fnmatch pattern over EvidenceRecord.claim
    min_kind: EvidenceKind = EvidenceKind.EMPIRICALLY_VALIDATED
    optional: bool = False              # missing is fine; present records are still checked
    bind_subject: bool = False          # satisfying records must have subject == artifact_digest
    zero_failed: bool = True            # any FAILED record matching the pattern denies
    producer: str | None = None         # e.g. "kernel": only records from this producer count

    def matches(self, claim: str) -> bool:
        return fnmatch.fnmatchcase(claim, self.claim_pattern)

    def to_dict(self) -> dict:
        return {
            "claim_pattern": self.claim_pattern,
            "min_kind": self.min_kind.value,
            "optional": self.optional,
            "bind_subject": self.bind_subject,
            "zero_failed": self.zero_failed,
            "producer": self.producer,
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "Requirement":
        return Requirement(
            claim_pattern=d["claim_pattern"],
            min_kind=EvidenceKind(d.get("min_kind", "EMPIRICALLY_VALIDATED")),
            optional=bool(d.get("optional", False)),
            bind_subject=bool(d.get("bind_subject", False)),
            zero_failed=bool(d.get("zero_failed", True)),
            producer=d.get("producer"),
        )


@dataclass
class Policy:
    required: list[Requirement] = field(default_factory=list)
    forbid_producers_for_kinds: dict[str, list[str]] = field(
        default_factory=lambda: {"ATTESTED": ["agent:*"], "PROVED_WITHIN_MODEL": ["agent:*"]}
    )
    max_age_s: int = 86400
    name: str = "release"

    def producer_forbidden(self, kind: EvidenceKind, producer: str) -> bool:
        pats = self.forbid_producers_for_kinds.get(kind.value, [])
        return any(fnmatch.fnmatchcase(producer, p) for p in pats)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "required": [r.to_dict() for r in self.required],
            "forbid_producers_for_kinds": self.forbid_producers_for_kinds,
            "max_age_s": self.max_age_s,
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "Policy":
        reqs = []
        for r in d.get("required", []):
            if isinstance(r, Requirement):
                reqs.append(r)
            else:
                reqs.append(Requirement.from_dict(r))
        return Policy(
            required=reqs,
            forbid_producers_for_kinds=dict(d.get("forbid_producers_for_kinds", {"ATTESTED": ["agent:*"]})),
            max_age_s=int(d.get("max_age_s", 86400)),
            name=d.get("name", "release"),
        )


def load_policy(path: str | Path = DEFAULT_POLICY_PATH) -> Policy:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return Policy.from_dict(data)


__all__ = ["Policy", "Requirement", "load_policy", "kind_satisfies", "DEFAULT_POLICY_PATH",
           "KERNEL_PRODUCER", "is_agent_producer"]
