"""Authorization kernel: decides AUTHORIZED / DENIED from a sealed bundle and a policy.

It trusts nothing it cannot check: the signature and chain must verify, every
required claim must be met by a record of sufficient certainty that is fresh,
about the right subject, produced by an allowed producer, and not contradicted
by a FAILED record.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from worldline.core import EvidenceKind

from .bundle import Bundle
from .policy import Policy, Requirement, kind_satisfies
from .records import EvidenceRecord, parse_iso

AUTHORIZED = "AUTHORIZED"
DENIED = "DENIED"


@dataclass
class Authorization:
    decision: str
    artifact_digest: str
    reasons: list[str] = field(default_factory=list)
    satisfied: list[dict] = field(default_factory=list)   # {claim_pattern, claim, record_id, kind, producer}
    missing: list[str] = field(default_factory=list)      # claim patterns with no acceptable record
    stale: list[dict] = field(default_factory=list)       # {record_id, claim, derived_at, age_s}
    downgraded: list[dict] = field(default_factory=list)  # {record_id, claim, kind, min_kind}
    evaluated_at: str = ""
    policy: str = ""

    @property
    def authorized(self) -> bool:
        return self.decision == AUTHORIZED

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "artifact_digest": self.artifact_digest,
            "reasons": list(self.reasons),
            "satisfied": list(self.satisfied),
            "missing": list(self.missing),
            "stale": list(self.stale),
            "downgraded": list(self.downgraded),
            "evaluated_at": self.evaluated_at,
            "policy": self.policy,
        }

    @staticmethod
    def from_dict(d: dict) -> "Authorization":
        return Authorization(**d)


def _as_dt(now: datetime | str) -> datetime:
    dt = parse_iso(now) if isinstance(now, str) else now
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _entry(req: Requirement, rec: EvidenceRecord) -> dict:
    return {
        "claim_pattern": req.claim_pattern,
        "claim": rec.claim,
        "record_id": rec.id,
        "kind": rec.kind.value,
        "producer": rec.producer,
        "subject": rec.subject,
        "assumptions": list(rec.assumptions),
    }


def authorize(
    bundle: Bundle,
    policy: Policy,
    artifact_digest: str,
    public_key: str | bytes,
    now: datetime | str,
) -> Authorization:
    now_dt = _as_dt(now)
    auth = Authorization(decision=DENIED, artifact_digest=artifact_digest,
                         evaluated_at=now_dt.isoformat(), policy=policy.name)
    reasons = auth.reasons

    # 1. Integrity: no signature, no decision.
    vr = bundle.verify(public_key)
    if not vr.ok:
        for f in vr.findings:
            reasons.append(f"integrity:{f.code}" + (f"@{f.index}" if f.index is not None else "") + f": {f.detail}")
        return auth

    records = list(bundle.records)

    # 2. Producer rules: an agent may never appear as the author of a kernel-only kind.
    for rec in records:
        if policy.producer_forbidden(rec.kind, rec.producer):
            reasons.append(f"forbidden_producer: record {rec.id} kind {rec.kind.value} by {rec.producer}")

    max_age = timedelta(seconds=policy.max_age_s)

    # 3. Each requirement.
    for req in policy.required:
        matching = [r for r in records if req.matches(r.claim)]

        failed = [r for r in matching if r.kind == EvidenceKind.FAILED]
        if failed and req.zero_failed:
            for r in failed:
                reasons.append(f"failed_record: {r.claim} ({r.id}) result={r.result}")

        acceptable: list[EvidenceRecord] = []
        for r in matching:
            if r.kind == EvidenceKind.FAILED:
                continue
            if req.bind_subject and r.subject != artifact_digest:
                reasons.append(f"subject_mismatch: {r.claim} ({r.id}) is about {r.subject}, not {artifact_digest}")
                continue
            if req.producer and r.producer != req.producer:
                reasons.append(f"wrong_producer: {r.claim} ({r.id}) by {r.producer}, policy needs {req.producer}")
                continue
            age = now_dt - parse_iso(r.derived_at)
            if age > max_age:
                auth.stale.append({"record_id": r.id, "claim": r.claim, "derived_at": r.derived_at,
                                   "age_s": int(age.total_seconds())})
                continue
            if not kind_satisfies(r.kind, req.min_kind):
                auth.downgraded.append({"record_id": r.id, "claim": r.claim, "kind": r.kind.value,
                                        "min_kind": req.min_kind.value})
                continue
            acceptable.append(r)

        if acceptable:
            auth.satisfied.extend(_entry(req, r) for r in acceptable)
        elif not req.optional:
            auth.missing.append(req.claim_pattern)
            reasons.append(f"missing: no acceptable record for {req.claim_pattern!r}")

    for s in auth.stale:
        reasons.append(f"stale: {s['claim']} ({s['record_id']}) is {s['age_s']}s old > {policy.max_age_s}s")
    for d in auth.downgraded:
        reasons.append(f"downgraded: {d['claim']} ({d['record_id']}) is {d['kind']} < {d['min_kind']}")

    # A stale/downgraded record that was the only candidate also shows up in `missing`;
    # one that has a fresh sibling does not block the decision but is still reported.
    blocking = [r for r in reasons if not (r.startswith("stale:") or r.startswith("downgraded:"))]
    if blocking or auth.missing:
        auth.decision = DENIED
    else:
        auth.decision = AUTHORIZED
        if not reasons:
            reasons.append(f"all {len(policy.required)} requirement(s) met by {len(auth.satisfied)} record(s)")
    return auth


def revoke_if_assumption_invalid(
    bundle: Bundle,
    invalidated_assumption: str,
    authorization: Authorization | None = None,
) -> Authorization:
    """Deny if any record that satisfied the authorization (or, with no prior
    authorization, any record in the bundle) rests on the invalidated assumption.
    Otherwise return the prior authorization unchanged (or an AUTHORIZED stub
    stating that no record depends on the assumption)."""
    if authorization is not None:
        satisfying_ids = {s["record_id"] for s in authorization.satisfied}
        candidates = [r for r in bundle.records if r.id in satisfying_ids]
        digest = authorization.artifact_digest
    else:
        candidates = list(bundle.records)
        digest = ""

    hit = [r for r in candidates if invalidated_assumption in r.assumptions]
    if not hit:
        if authorization is not None:
            return authorization
        return Authorization(decision=AUTHORIZED, artifact_digest=digest,
                             reasons=[f"no record depends on assumption {invalidated_assumption!r}"])

    base = authorization.to_dict() if authorization is not None else {}
    out = Authorization(
        decision=DENIED,
        artifact_digest=digest,
        reasons=[f"assumption_invalidated: {invalidated_assumption!r} used by {r.claim} ({r.id})" for r in hit],
        satisfied=[s for s in base.get("satisfied", []) if s["record_id"] not in {r.id for r in hit}],
        missing=sorted({s["claim_pattern"] for s in base.get("satisfied", []) if s["record_id"] in {r.id for r in hit}}),
        stale=base.get("stale", []),
        downgraded=base.get("downgraded", []),
        evaluated_at=base.get("evaluated_at", ""),
        policy=base.get("policy", ""),
    )
    return out
