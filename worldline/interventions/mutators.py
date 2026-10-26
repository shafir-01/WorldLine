"""Mutators propose concrete interventions from a baseline trace; abstractize/concretize move
between concrete {"event_id"} targets and portable {"kind","source_id","nth"[, "role"]} selectors.

All randomness goes through the DeterministicRNG passed in; the same (trace, rng) gives the
same proposals.
"""
from __future__ import annotations

from typing import Any, Sequence

from worldline.core import DeterministicRNG, Event

from .ops import Intervention, resolve

NS = 1_000_000_000
DEFAULT_DELAYS_NS = (NS, 5 * NS, 30 * NS, 60 * NS, 300 * NS)
DEFAULT_SKEWS_NS = (-60 * NS, -5 * NS, 5 * NS, 60 * NS)


def _truth_events(trace: Any) -> list[Event]:
    return list(trace.truth.ordered())


def _roles(trace: Any) -> list[str]:
    return sorted(getattr(trace, "views", {}) or {})


def _pick_events(events: Sequence[Event], rng: DeterministicRNG, budget: int) -> list[Event]:
    if not events or budget <= 0:
        return []
    if budget >= len(events):
        return list(events)
    return rng.sample(list(events), budget)


class Mutator:
    op: str = ""

    def propose(self, trace: Any, rng: DeterministicRNG, budget: int) -> list[Intervention]:  # pragma: no cover
        raise NotImplementedError

    def _maybe_role(self, trace: Any, rng: DeterministicRNG) -> str | None:
        roles = _roles(trace)
        if not roles:
            return None
        pick = rng.choice([None, *roles])
        return pick


class DelayMutator(Mutator):
    op = "delay"

    def __init__(self, delays_ns: Sequence[int] = DEFAULT_DELAYS_NS):
        self.delays_ns = tuple(delays_ns)

    def propose(self, trace, rng, budget):
        out = []
        for ev in _pick_events(_truth_events(trace), rng, budget):
            params = {"by_ns": rng.choice(self.delays_ns)}
            role = self._maybe_role(trace, rng)
            if role:
                params["role"] = role
            out.append(Intervention("delay", {"event_id": ev.id}, params))
        return out


class DropMutator(Mutator):
    op = "drop"

    def propose(self, trace, rng, budget):
        out = []
        for ev in _pick_events(_truth_events(trace), rng, budget):
            params = {}
            role = self._maybe_role(trace, rng)
            if role:
                params["role"] = role
            out.append(Intervention("drop", {"event_id": ev.id}, params))
        return out


class DuplicateMutator(Mutator):
    op = "duplicate"

    def __init__(self, max_count: int = 3, spacing_ns: int = 0):
        self.max_count = max_count
        self.spacing_ns = spacing_ns

    def propose(self, trace, rng, budget):
        out = []
        for ev in _pick_events(_truth_events(trace), rng, budget):
            params = {"count": rng.randint(1, self.max_count)}
            if self.spacing_ns:
                params["spacing_ns"] = self.spacing_ns
            out.append(Intervention("duplicate", {"event_id": ev.id}, params))
        return out


class ReorderMutator(Mutator):
    """Push an event past the next truth event of the same kind (by_ns fixed at proposal time)."""
    op = "reorder"

    def __init__(self, margin_ns: int = 1):
        self.margin_ns = margin_ns

    def propose(self, trace, rng, budget):
        events = _truth_events(trace)
        candidates = []
        for i, ev in enumerate(events):
            nxt = next((e for e in events[i + 1:] if e.kind == ev.kind), None)
            if nxt is not None:
                candidates.append((ev, nxt))
        out = []
        for ev, nxt in _pick_events(candidates, rng, budget):
            by = nxt.event_time_ns - ev.event_time_ns + self.margin_ns
            out.append(Intervention("reorder", {"event_id": ev.id}, {"by_ns": by}))
        return out


class CorruptMutator(Mutator):
    """Alter one scalar payload field. Corrupted copies carry quality=0.0 and self-provenance."""
    op = "corrupt"

    def propose(self, trace, rng, budget):
        candidates = [ev for ev in _truth_events(trace)
                      if any(isinstance(v, (int, float, str, bool)) for v in ev.payload.values())]
        out = []
        for ev in _pick_events(candidates, rng, budget):
            keys = sorted(k for k, v in ev.payload.items() if isinstance(v, (int, float, str, bool)))
            key = rng.choice(keys)
            out.append(Intervention("corrupt", {"event_id": ev.id},
                                    {"field": f"payload.{key}", "value": _corrupt_value(ev.payload[key], rng)}))
        return out


def _corrupt_value(v: Any, rng: DeterministicRNG) -> Any:
    if isinstance(v, bool):
        return not v
    if isinstance(v, int):
        return rng.choice([0, -v, v * 10, v + 1])
    if isinstance(v, float):
        return rng.choice([0.0, -v, min(1.0, v * 2) if 0 <= v <= 1 else v * 10, 1.0 - v if 0 <= v <= 1 else v / 2])
    return rng.choice(["", "CORRUPT", str(v)[::-1]])


class RevokeMutator(Mutator):
    """Cut a role off from all deliveries from some truth time onward."""
    op = "revoke"

    def propose(self, trace, rng, budget):
        roles = _roles(trace)
        events = _truth_events(trace)
        if not roles or not events:
            return []
        out = []
        for ev in _pick_events(events, rng, budget):
            out.append(Intervention("revoke", {}, {"role": rng.choice(roles), "from_ns": ev.event_time_ns}))
        return out


class ClockSkewMutator(Mutator):
    op = "clock_skew"

    def __init__(self, skews_ns: Sequence[int] = DEFAULT_SKEWS_NS):
        self.skews_ns = tuple(skews_ns)

    def propose(self, trace, rng, budget):
        sources = sorted({e.source_id for e in _truth_events(trace)})
        if not sources:
            return []
        out = []
        for _ in range(budget):
            out.append(Intervention("clock_skew", {}, {"source_id": rng.choice(sources),
                                                        "offset_ns": rng.choice(self.skews_ns)}))
        return out


ALL_MUTATORS: tuple[type[Mutator], ...] = (DelayMutator, DropMutator, DuplicateMutator, ReorderMutator,
                                           CorruptMutator, RevokeMutator, ClockSkewMutator)


def default_mutators() -> list[Mutator]:
    return [m() for m in ALL_MUTATORS]


# ---------------------------------------------------------------- abstract <-> concrete

def abstractize(intervention: Intervention, trace_or_events: Any) -> Intervention:
    """Replace a concrete {"event_id"} target by {"kind","source_id","nth"[, "role"]}.

    nth is the 0-based index among truth events with the same kind and source_id, in canonical
    order. Untargeted or already-abstract interventions are returned unchanged.
    """
    if not intervention.is_concrete:
        return intervention
    events = _events_of(trace_or_events)
    eid = intervention.target["event_id"]
    ev = next((e for e in events if e.id == eid), None)
    if ev is None:
        raise KeyError(f"event {eid!r} not in trace truth; cannot abstractize")
    peers = resolve({"kind": ev.kind, "source_id": ev.source_id}, events)
    target: dict = {"kind": ev.kind, "source_id": ev.source_id, "nth": peers.index(eid)}
    if intervention.target.get("role") is not None:
        target["role"] = intervention.target["role"]
    return Intervention(intervention.op, target, dict(intervention.params))


def concretize(abstract: Intervention, events_or_trace: Any) -> Intervention | None:
    """Resolve an abstract target on another trace. None when nothing matches."""
    if abstract.is_concrete or not {k for k in abstract.target if k != "role"}:
        return abstract
    events = _events_of(events_or_trace)
    ids = resolve(abstract.target, events)
    if not ids:
        return None
    target: dict = {"event_id": ids[0]}
    if abstract.target.get("role") is not None:
        target["role"] = abstract.target["role"]
    return Intervention(abstract.op, target, dict(abstract.params))


def _events_of(x: Any) -> list[Event]:
    if hasattr(x, "truth"):
        return list(x.truth.ordered())
    if hasattr(x, "ordered"):
        return list(x.ordered())
    return sorted(x, key=Event.sort_key)
