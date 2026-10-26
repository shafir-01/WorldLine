"""Bundle: hash-chained EvidenceRecords sealed by an Ed25519 checkpoint.

Chain: link[i] = {prev_hash: link[i-1].hash, hash: H(prev_hash || record.id)}.
A checkpoint signs (first_index, last_index, root, signer_key_id, sealed_at).
Keys are never stored in a bundle; only the signer's key id (hash of the
public key) is, so a verifier can tell "wrong key" from "forged signature".
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from worldline.core import canonical_json, content_hash

from .records import EvidenceRecord, utc_now_iso

GENESIS = "sha256:" + "0" * 64

# Finding codes (VerificationResult.findings[*].code)
RECORD_EDITED = "record_edited"          # record content no longer matches its id / link hash
RECORD_DROPPED = "record_dropped"        # chain break: a link's prev_hash points to nothing present
RECORDS_REORDERED = "records_reordered"  # all links present but out of order
UNKNOWN_SIGNER = "unknown_signer"        # checkpoint.signer_key_id != key_id(public_key)
SIGNATURE_INVALID = "signature_invalid"  # key id matches but Ed25519 verify fails
TRUNCATED = "truncated"                  # fewer records than the checkpoint claims
UNSEALED_TAIL = "unsealed_tail"          # records appended after the sealed range
ROOT_MISMATCH = "root_mismatch"          # chain intact but final hash != checkpoint.root
NOT_SEALED = "not_sealed"                # no checkpoint at all


# ---------------------------------------------------------------- keys


def generate_keypair() -> tuple[str, str]:
    """Return (private_pem, public_pem) for a fresh Ed25519 key."""
    priv = Ed25519PrivateKey.generate()
    private_pem = priv.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public_pem = priv.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode()
    return private_pem, public_pem


def _load_private(key: str | bytes | Ed25519PrivateKey) -> Ed25519PrivateKey:
    if isinstance(key, Ed25519PrivateKey):
        return key
    data = key.encode() if isinstance(key, str) else key
    k = serialization.load_pem_private_key(data, password=None)
    if not isinstance(k, Ed25519PrivateKey):
        raise TypeError("signing key must be Ed25519")
    return k


def _load_public(key: str | bytes | Ed25519PublicKey) -> Ed25519PublicKey:
    if isinstance(key, Ed25519PublicKey):
        return key
    data = key.encode() if isinstance(key, str) else key
    k = serialization.load_pem_public_key(data)
    if not isinstance(k, Ed25519PublicKey):
        raise TypeError("public key must be Ed25519")
    return k


def key_id(public_key: str | bytes | Ed25519PublicKey) -> str:
    """Stable identifier for a public key: sha256 of its raw 32 bytes."""
    raw = _load_public(public_key).public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return "ed25519:" + hashlib.sha256(raw).hexdigest()[:32]


# ---------------------------------------------------------------- data shapes


def _link_hash(prev_hash: str, record_id: str) -> str:
    return "sha256:" + hashlib.sha256((prev_hash + record_id).encode()).hexdigest()


@dataclass(frozen=True)
class ChainLink:
    prev_hash: str
    hash: str

    def to_dict(self) -> dict:
        return {"prev_hash": self.prev_hash, "hash": self.hash}


@dataclass(frozen=True)
class Checkpoint:
    first_index: int
    last_index: int
    root: str
    signer_key_id: str
    sealed_at: str
    signature: str  # hex

    def signed_body(self) -> bytes:
        return canonical_json(
            {
                "first_index": self.first_index,
                "last_index": self.last_index,
                "root": self.root,
                "signer_key_id": self.signer_key_id,
                "sealed_at": self.sealed_at,
            }
        ).encode()

    def to_dict(self) -> dict:
        return {
            "first_index": self.first_index,
            "last_index": self.last_index,
            "root": self.root,
            "signer_key_id": self.signer_key_id,
            "sealed_at": self.sealed_at,
            "signature": self.signature,
        }

    @staticmethod
    def from_dict(d: dict) -> "Checkpoint":
        return Checkpoint(**d)


@dataclass
class Finding:
    code: str
    index: int | None
    detail: str

    def to_dict(self) -> dict:
        return {"code": self.code, "index": self.index, "detail": self.detail}


@dataclass
class VerificationResult:
    ok: bool
    findings: list[Finding] = field(default_factory=list)
    verified_root: str | None = None
    signer_key_id: str | None = None

    @property
    def codes(self) -> list[str]:
        return [f.code for f in self.findings]

    def has(self, code: str) -> bool:
        return code in self.codes

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "findings": [f.to_dict() for f in self.findings],
            "verified_root": self.verified_root,
            "signer_key_id": self.signer_key_id,
        }


# ---------------------------------------------------------------- bundle


class Bundle:
    def __init__(self) -> None:
        self.records: list[EvidenceRecord] = []
        self.links: list[ChainLink] = []
        self.checkpoint: Checkpoint | None = None

    # -- building

    def add(self, rec: EvidenceRecord) -> str:
        """Append a record. Rejects agent-produced kernel-only kinds and
        appending after a seal (seal again to cover a longer range)."""
        if not isinstance(rec, EvidenceRecord):
            raise TypeError("Bundle.add expects an EvidenceRecord")
        rec.validate_producer()
        prev = self.links[-1].hash if self.links else GENESIS
        link = ChainLink(prev_hash=prev, hash=_link_hash(prev, rec.id))
        self.records.append(rec)
        self.links.append(link)
        return link.hash

    def extend(self, recs: list[EvidenceRecord]) -> None:
        for r in recs:
            self.add(r)

    @property
    def root(self) -> str:
        return self.links[-1].hash if self.links else GENESIS

    def __len__(self) -> int:
        return len(self.records)

    def __iter__(self):
        return iter(self.records)

    def seal(self, signing_key: str | bytes | Ed25519PrivateKey, *, sealed_at: str | None = None) -> Checkpoint:
        if not self.records:
            raise ValueError("cannot seal an empty bundle")
        priv = _load_private(signing_key)
        unsigned = Checkpoint(
            first_index=0,
            last_index=len(self.records) - 1,
            root=self.root,
            signer_key_id=key_id(priv.public_key()),
            sealed_at=sealed_at or utc_now_iso(),
            signature="",
        )
        sig = priv.sign(unsigned.signed_body()).hex()
        self.checkpoint = Checkpoint(**{**unsigned.to_dict(), "signature": sig})
        return self.checkpoint

    # -- verification

    def verify(self, public_key: str | bytes | Ed25519PublicKey) -> VerificationResult:
        findings: list[Finding] = []
        cp = self.checkpoint
        if cp is None:
            return VerificationResult(ok=False, findings=[Finding(NOT_SEALED, None, "bundle has no checkpoint")])

        # 1. signer identity, then signature
        my_id = key_id(public_key)
        if cp.signer_key_id != my_id:
            findings.append(Finding(UNKNOWN_SIGNER, None,
                                    f"checkpoint signed by {cp.signer_key_id}, verifier key is {my_id}"))
        else:
            try:
                _load_public(public_key).verify(bytes.fromhex(cp.signature), cp.signed_body())
            except (InvalidSignature, ValueError):
                findings.append(Finding(SIGNATURE_INVALID, None, "Ed25519 signature does not verify"))

        # 2. length vs checkpoint range
        n = len(self.records)
        if n != len(self.links):
            findings.append(Finding(RECORD_DROPPED, None, f"{n} records but {len(self.links)} chain links"))
        if n <= cp.last_index:
            findings.append(Finding(TRUNCATED, n,
                                    f"checkpoint covers indices 0..{cp.last_index} but only {n} records present"))
        elif n > cp.last_index + 1:
            findings.append(Finding(UNSEALED_TAIL, cp.last_index + 1,
                                    f"{n - cp.last_index - 1} record(s) after sealed index {cp.last_index}"))

        # 3. per-record integrity and chain structure
        sealed = min(n, len(self.links), cp.last_index + 1)
        structural: list[Finding] = []
        for i in range(sealed):
            rec, link = self.records[i], self.links[i]
            if content_hash(rec.body()) != rec.id or _link_hash(link.prev_hash, rec.id) != link.hash:
                structural.append(Finding(RECORD_EDITED, i, f"record {i} content does not match its hash"))
        if not structural:
            structural.extend(self._chain_findings(sealed))
        findings.extend(structural)

        # 4. root
        verified_root = None
        if not findings or all(f.code in (UNSEALED_TAIL,) for f in findings):
            root = self.links[cp.last_index].hash
            if root != cp.root:
                findings.append(Finding(ROOT_MISMATCH, cp.last_index, f"chain root {root} != checkpoint root {cp.root}"))
            else:
                verified_root = root

        return VerificationResult(ok=not findings, findings=findings,
                                  verified_root=verified_root, signer_key_id=cp.signer_key_id)

    def _chain_findings(self, upto: int) -> list[Finding]:
        """Distinguish a dropped record from a reorder over links[0:upto]."""
        out: list[Finding] = []
        hashes = {l.hash for l in self.links[:upto]}
        for i in range(upto):
            link = self.links[i]
            expected_prev = GENESIS if i == 0 else self.links[i - 1].hash
            if link.prev_hash == expected_prev:
                continue
            if link.prev_hash in hashes or (link.prev_hash == GENESIS):
                out.append(Finding(RECORDS_REORDERED, i,
                                   f"link {i} follows a record that is present but not at index {i - 1}"))
            else:
                out.append(Finding(RECORD_DROPPED, i,
                                   f"link {i} expects predecessor {link.prev_hash[:20]}... which is missing"))
        return out

    # -- serialization (no keys ever included)

    def to_dict(self) -> dict:
        return {
            "format": "worldline.bundle.v1",
            "records": [r.to_dict() for r in self.records],
            "links": [l.to_dict() for l in self.links],
            "checkpoint": self.checkpoint.to_dict() if self.checkpoint else None,
        }

    def to_json(self, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=True)

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "Bundle":
        """Load as stored, without re-deriving. verify() judges what was loaded.
        Records that fail content/id checks are kept (so verify can report them)."""
        b = Bundle()
        for rd in d.get("records", []):
            b.records.append(_load_record_lenient(rd))
        b.links = [ChainLink(**l) for l in d.get("links", [])]
        if d.get("checkpoint"):
            b.checkpoint = Checkpoint.from_dict(d["checkpoint"])
        return b

    @staticmethod
    def from_json(s: str) -> "Bundle":
        return Bundle.from_dict(json.loads(s))


def _load_record_lenient(rd: dict) -> EvidenceRecord:
    """Build a record keeping the stored id even if content no longer matches,
    so tampering surfaces as a RECORD_EDITED finding rather than a load error."""
    stored_id = rd.get("id", "")
    body = {k: v for k, v in rd.items() if k != "id"}
    rec = EvidenceRecord.from_dict(body)
    if stored_id and stored_id != rec.id:
        object.__setattr__(rec, "id", stored_id)
    return rec
