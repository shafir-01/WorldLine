import json
from datetime import datetime, timedelta, timezone

import pytest

from worldline.core import EvidenceKind
from worldline.evidence import (
    AUTHORIZED,
    DENIED,
    MISMATCH_CLAIM,
    RECORD_DROPPED,
    RECORD_EDITED,
    RECORDS_REORDERED,
    SIGNATURE_INVALID,
    TRUNCATED,
    UNKNOWN_SIGNER,
    UNSEALED_TAIL,
    Authorization,
    Bundle,
    EvidenceRecord,
    authorize,
    generate_keypair,
    key_id,
    load_policy,
    recompute_and_attest,
    revoke_if_assumption_invalid,
)

ART = "sha256:" + "ab" * 32
OTHER = "sha256:" + "cd" * 32
T0 = "2026-10-26T12:00:00Z"
NOW = datetime(2026, 10, 26, 13, 0, tzinfo=timezone.utc)


def rec(claim, kind=EvidenceKind.EMPIRICALLY_VALIDATED, subject=ART, producer="agent:investigator",
        result=None, assumptions=(), derived_at=T0):
    return EvidenceRecord(kind=kind, claim=claim, subject=subject, procedure="pytest",
                          inputs_hash="sha256:" + "11" * 32, result=result or {"status": "pass"},
                          assumptions=list(assumptions), derived_at=derived_at, producer=producer)


@pytest.fixture
def keys():
    return generate_keypair()


@pytest.fixture
def policy():
    return load_policy()


def good_bundle(priv, *, tests_result=None):
    b = Bundle()
    b.add(rec("tests:unit", result=tests_result or {"passed": 42, "failed": 0}))
    b.add(rec("invariant:alert_ack@1", assumptions=["bounded horizon 3600s"]))
    b.add(rec("invariant:no_leak@1"))
    recompute_and_attest(
        b,
        {"tests:unit": tests_result or {"passed": 42, "failed": 0}, "artifact:digest": {"digest": ART}},
        priv, derived_at=T0, subject=ART,
    )
    return b


# ---------------------------------------------------------------- records


def test_record_id_is_content_hash_and_tamper_evident():
    r = rec("tests:unit")
    assert r.id.startswith("sha256:")
    assert EvidenceRecord.from_dict(r.to_dict()) == r
    with pytest.raises(ValueError):
        EvidenceRecord.from_dict({**r.to_dict(), "result": {"status": "fail"}})


def test_agent_cannot_emit_attested_or_proved():
    b = Bundle()
    with pytest.raises(ValueError, match="only 'kernel'"):
        b.add(rec("tests:unit", kind=EvidenceKind.ATTESTED))
    with pytest.raises(ValueError, match="only 'kernel'"):
        b.add(rec("tests:unit", kind=EvidenceKind.PROVED_WITHIN_MODEL))
    b.add(rec("tests:unit", kind=EvidenceKind.FAILED))
    b.add(rec("tests:unit", kind=EvidenceKind.UNKNOWN))
    b.add(rec("tests:unit", kind=EvidenceKind.ATTESTED, producer="kernel"))
    assert len(b) == 3


# ---------------------------------------------------------------- bundle / verify


def test_happy_path_verifies_and_authorizes(keys, policy):
    priv, pub = keys
    b = good_bundle(priv)
    vr = b.verify(pub)
    assert vr.ok, vr.to_dict()
    assert vr.verified_root == b.root
    auth = authorize(b, policy, ART, pub, NOW)
    assert auth.decision == AUTHORIZED, auth.reasons
    assert auth.missing == [] and auth.stale == [] and auth.downgraded == []
    claims = {s["claim"] for s in auth.satisfied}
    assert {"tests:unit", "invariant:alert_ack@1", "invariant:no_leak@1", "artifact:digest"} <= claims
    json.dumps(auth.to_dict())  # JSON-serializable


def test_fresh_load_from_json_verifies_without_private_key(keys):
    priv, pub = keys
    b = good_bundle(priv)
    s = b.to_json()
    assert "PRIVATE" not in s and "BEGIN" not in s  # no key material in bundle
    b2 = Bundle.from_json(s)
    assert b2.verify(pub).ok
    assert b2.root == b.root and b2.checkpoint == b.checkpoint
    assert key_id(pub) == b2.checkpoint.signer_key_id


def test_tamper_edited_record(keys, policy):
    priv, pub = keys
    d = good_bundle(priv).to_dict()
    d["records"][1]["result"] = {"status": "pass", "forged": True}
    b = Bundle.from_dict(d)
    vr = b.verify(pub)
    assert not vr.ok and vr.has(RECORD_EDITED)
    assert [f.index for f in vr.findings if f.code == RECORD_EDITED] == [1]
    assert authorize(b, policy, ART, pub, NOW).decision == DENIED


def test_tamper_dropped_record(keys, policy):
    priv, pub = keys
    d = good_bundle(priv).to_dict()
    del d["records"][1]; del d["links"][1]
    b = Bundle.from_dict(d)
    vr = b.verify(pub)
    assert not vr.ok and vr.has(RECORD_DROPPED)
    assert not vr.has(RECORD_EDITED) and not vr.has(RECORDS_REORDERED)
    assert authorize(b, policy, ART, pub, NOW).decision == DENIED


def test_tamper_reordered_records(keys, policy):
    priv, pub = keys
    d = good_bundle(priv).to_dict()
    d["records"][0], d["records"][1] = d["records"][1], d["records"][0]
    d["links"][0], d["links"][1] = d["links"][1], d["links"][0]
    b = Bundle.from_dict(d)
    vr = b.verify(pub)
    assert not vr.ok and vr.has(RECORDS_REORDERED)
    assert not vr.has(RECORD_DROPPED) and not vr.has(RECORD_EDITED)
    assert authorize(b, policy, ART, pub, NOW).decision == DENIED


def test_tamper_signature_invalid(keys, policy):
    priv, pub = keys
    d = good_bundle(priv).to_dict()
    sig = bytearray(bytes.fromhex(d["checkpoint"]["signature"]))
    sig[0] ^= 0xFF
    d["checkpoint"]["signature"] = sig.hex()
    b = Bundle.from_dict(d)
    vr = b.verify(pub)
    assert not vr.ok and vr.codes == [SIGNATURE_INVALID]
    assert authorize(b, policy, ART, pub, NOW).decision == DENIED


def test_tamper_wrong_key_is_unknown_signer(keys, policy):
    priv, pub = keys
    _, other_pub = generate_keypair()
    b = good_bundle(priv)
    vr = b.verify(other_pub)
    assert not vr.ok and vr.codes == [UNKNOWN_SIGNER]
    assert authorize(b, policy, ART, other_pub, NOW).decision == DENIED


def test_tamper_truncated_tail(keys, policy):
    priv, pub = keys
    d = good_bundle(priv).to_dict()
    d["records"].pop(); d["links"].pop()
    b = Bundle.from_dict(d)
    vr = b.verify(pub)
    assert not vr.ok and vr.has(TRUNCATED)
    assert not vr.has(RECORD_DROPPED)
    assert authorize(b, policy, ART, pub, NOW).decision == DENIED


def test_unsealed_tail_is_reported(keys, policy):
    priv, pub = keys
    b = good_bundle(priv)
    b.add(rec("invariant:late@1"))
    vr = b.verify(pub)
    assert not vr.ok and vr.has(UNSEALED_TAIL)
    assert authorize(b, policy, ART, pub, NOW).decision == DENIED


# ---------------------------------------------------------------- policy decisions


def test_stale_evidence_denied(keys, policy):
    priv, pub = keys
    b = good_bundle(priv)
    later = NOW + timedelta(seconds=policy.max_age_s + 1)
    auth = authorize(b, policy, ART, pub, later)
    assert auth.decision == DENIED
    assert auth.stale and "tests:unit" in auth.missing


def test_wrong_artifact_digest_denied(keys, policy):
    priv, pub = keys
    b = good_bundle(priv)
    auth = authorize(b, policy, OTHER, pub, NOW)
    assert auth.decision == DENIED
    assert "artifact:digest" in auth.missing and "tests:unit" in auth.missing
    assert any(r.startswith("subject_mismatch") for r in auth.reasons)


def test_downgraded_kind_denied(keys, policy):
    priv, pub = keys
    b = Bundle()
    b.add(rec("tests:unit"))
    b.add(rec("invariant:x@1", kind=EvidenceKind.UNKNOWN))
    recompute_and_attest(b, {"artifact:digest": {"digest": ART}}, priv, derived_at=T0, subject=ART)
    auth = authorize(b, policy, ART, pub, NOW)
    assert auth.decision == DENIED
    assert auth.downgraded and auth.downgraded[0]["claim"] == "invariant:x@1"


def test_failed_invariant_denies(keys, policy):
    priv, pub = keys
    b = good_bundle(priv)
    d = b.to_dict()
    b = Bundle.from_dict(d)
    b.checkpoint = None
    b.add(rec("invariant:no_leak@1", kind=EvidenceKind.FAILED, result={"witness": ["e1", "e2"]}))
    b.seal(priv, sealed_at=T0)
    auth = authorize(b, policy, ART, pub, NOW)
    assert auth.decision == DENIED
    assert any(r.startswith("failed_record") for r in auth.reasons)


def test_agent_report_mismatch_produces_failed_and_denies(keys, policy):
    priv, pub = keys
    b = Bundle()
    b.add(rec("tests:unit", result={"passed": 42, "failed": 0}))
    b.add(rec("invariant:alert_ack@1"))
    appended = recompute_and_attest(
        b, {"tests:unit": {"passed": 40, "failed": 2}}, priv, derived_at=T0
    )
    assert appended[0].kind == EvidenceKind.FAILED
    assert appended[0].claim == MISMATCH_CLAIM
    assert appended[0].result["agent_reported"] == {"passed": 42, "failed": 0}
    assert appended[0].result["kernel_recomputed"] == {"passed": 40, "failed": 2}
    assert b.verify(pub).ok
    auth = authorize(b, policy, ART, pub, NOW)
    assert auth.decision == DENIED
    assert any(MISMATCH_CLAIM in r for r in auth.reasons)


def test_kernel_attests_only_matching_results(keys):
    priv, pub = keys
    b = Bundle()
    b.add(rec("tests:unit", result={"passed": 1}))
    out = recompute_and_attest(b, {"tests:unit": {"passed": 1}}, priv, derived_at=T0)
    assert out[0].kind == EvidenceKind.ATTESTED and out[0].producer == "kernel"
    assert out[0].subject == ART
    assert b.verify(pub).ok


def test_assumption_revocation_flips_to_denied(keys, policy):
    priv, pub = keys
    b = good_bundle(priv)
    auth = authorize(b, policy, ART, pub, NOW)
    assert auth.decision == AUTHORIZED
    same = revoke_if_assumption_invalid(b, "delivery jitter <= 30s", auth)
    assert same.decision == AUTHORIZED
    revoked = revoke_if_assumption_invalid(b, "bounded horizon 3600s", auth)
    assert revoked.decision == DENIED
    assert "invariant:*" in revoked.missing
    assert any("assumption_invalidated" in r for r in revoked.reasons)
    json.dumps(revoked.to_dict())
    # without a prior authorization, scans the whole bundle
    assert revoke_if_assumption_invalid(b, "bounded horizon 3600s").decision == DENIED


def test_policy_loads_from_yaml(policy):
    pats = [r.claim_pattern for r in policy.required]
    assert "tests:unit" in pats and "invariant:*" in pats and "artifact:digest" in pats
    assert policy.max_age_s == 86400
    assert policy.producer_forbidden(EvidenceKind.ATTESTED, "agent:repairer")
    assert not policy.producer_forbidden(EvidenceKind.ATTESTED, "kernel")
    assert Authorization.from_dict(Authorization(decision=DENIED, artifact_digest=ART).to_dict()).decision == DENIED
