"""Kernel re-derivation: the only path to ATTESTED evidence.

Agents report results. The kernel recomputes what it can and attests only when
its own result equals the agent's. A disagreement is recorded as FAILED under
"integrity:agent_report_mismatch" with both values, which denies the release.
"""
from __future__ import annotations

from typing import Any

from worldline.core import EvidenceKind, canonical_json, content_hash

from .bundle import Bundle, Checkpoint
from .records import KERNEL_PRODUCER, EvidenceRecord, is_agent_producer, utc_now_iso

MISMATCH_CLAIM = "integrity:agent_report_mismatch"


def _same(a: Any, b: Any) -> bool:
    return canonical_json(a) == canonical_json(b)


def recompute_and_attest(
    bundle: Bundle,
    recomputed: dict[str, Any],
    signing_key: str | bytes,
    *,
    procedure: str = "kernel.recompute",
    derived_at: str | None = None,
    seal: bool = True,
    subject: str | None = None,
) -> list[EvidenceRecord]:
    """For each claim in `recomputed`, compare against the latest agent-reported
    record for that claim and append a kernel record:

      agent result == recomputed -> ATTESTED (claim, subject copied from agent record)
      agent result != recomputed -> FAILED under MISMATCH_CLAIM with both values
      no agent record            -> ATTESTED on the kernel's own authority (subject = `subject` or "")

    Re-seals the bundle with `signing_key` unless seal=False. Returns the appended records.
    """
    ts = derived_at or utc_now_iso()
    appended: list[EvidenceRecord] = []

    for claim, result in recomputed.items():
        agent_recs = [r for r in bundle.records if r.claim == claim and is_agent_producer(r.producer)]
        agent = agent_recs[-1] if agent_recs else None
        rec_subject = agent.subject if agent else (subject or "")
        inputs_hash = agent.inputs_hash if agent else content_hash({"claim": claim})
        result_dict = result if isinstance(result, dict) else {"value": result}

        if agent is None or _same(agent.result, result_dict):
            rec = EvidenceRecord(
                kind=EvidenceKind.ATTESTED,
                claim=claim,
                subject=rec_subject,
                procedure=procedure,
                inputs_hash=inputs_hash,
                result=result_dict,
                assumptions=list(agent.assumptions) if agent else [],
                derived_at=ts,
                producer=KERNEL_PRODUCER,
            )
        else:
            rec = EvidenceRecord(
                kind=EvidenceKind.FAILED,
                claim=MISMATCH_CLAIM,
                subject=rec_subject,
                procedure=procedure,
                inputs_hash=inputs_hash,
                result={
                    "claim": claim,
                    "agent_producer": agent.producer,
                    "agent_record_id": agent.id,
                    "agent_reported": agent.result,
                    "kernel_recomputed": result_dict,
                },
                assumptions=[],
                derived_at=ts,
                producer=KERNEL_PRODUCER,
            )
        bundle.add(rec)
        appended.append(rec)

    if seal and bundle.records:
        bundle.seal(signing_key, sealed_at=ts)
    return appended


__all__ = ["recompute_and_attest", "MISMATCH_CLAIM", "Checkpoint"]
