"""Causal diff between two traces of the same scenario.

Reports exactly what differs between two worlds: which events a role received
in one world and not the other, how much information lag moved, and which
decisions changed and on what knowledge. Duck-typed over the Trace contract.
"""
from __future__ import annotations

from typing import Any


def _by_id(log) -> dict[str, Any]:
    return {e.id: e for e in log}


def causal_diff(trace_a, trace_b) -> dict:
    out: dict[str, Any] = {
        "a": trace_a.branch_id,
        "b": trace_b.branch_id,
        "truth_identical": trace_a.truth.digest == trace_b.truth.digest,
        "views": {},
        "decisions": [],
    }
    roles = sorted(set(trace_a.views) | set(trace_b.views))
    for role in roles:
        va = _by_id(trace_a.views.get(role, []))
        vb = _by_id(trace_b.views.get(role, []))
        only_a = sorted(set(va) - set(vb))
        only_b = sorted(set(vb) - set(va))
        moved = []
        for eid in sorted(set(va) & set(vb)):
            ra, rb = va[eid].received_time_ns, vb[eid].received_time_ns
            if ra != rb:
                moved.append({"event_id": eid, "received_a_ns": ra, "received_b_ns": rb,
                              "delta_ns": (rb - ra) if (ra is not None and rb is not None) else None})
        changed_payload = [eid for eid in sorted(set(va) & set(vb))
                           if va[eid].payload != vb[eid].payload]
        out["views"][role] = {"only_in_a": only_a, "only_in_b": only_b,
                              "received_time_moved": moved, "payload_changed": changed_payload}

    da = {(d.payload.get("actor"), d.payload.get("rule_id")): d for d in trace_a.decisions}
    db = {(d.payload.get("actor"), d.payload.get("rule_id")): d for d in trace_b.decisions}
    for key in sorted(set(da) | set(db), key=str):
        a, b = da.get(key), db.get(key)
        if a is None or b is None or a.payload.get("option") != b.payload.get("option"):
            ka = set(a.payload.get("known_event_ids", [])) if a else set()
            kb = set(b.payload.get("known_event_ids", [])) if b else set()
            out["decisions"].append({
                "actor": key[0], "rule_id": key[1],
                "option_a": a.payload.get("option") if a else None,
                "option_b": b.payload.get("option") if b else None,
                "known_only_in_a": sorted(ka - kb), "known_only_in_b": sorted(kb - ka),
            })
    out["differs"] = bool(out["decisions"]) or any(
        v["only_in_a"] or v["only_in_b"] or v["received_time_moved"] or v["payload_changed"]
        for v in out["views"].values())
    return out
