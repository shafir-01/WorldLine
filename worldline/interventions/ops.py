"""Intervention operators: how a FaultSchedule perturbs delivery of truth events to roles.

The worlds runner owns the loop (truth event x role). For each delivery it calls the hook built
by `hook_for_schedule`:

    hook(event, role, received_time_ns, rng) -> list[tuple[Event, int | None]]

Each tuple is one delivery: the (possibly altered) event and the time the role receives it,
or None when delivery is suppressed. The runner appends every (event, t) with t not None to the
role's view with `event.with_receipt(t)`.

Interventions never touch truth. They only change what a role sees, and when.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Sequence

from worldline.core import DeterministicRNG, Event, content_hash

Selector = dict
OPS: tuple[str, ...] = ("delay", "drop", "duplicate", "reorder", "corrupt", "revoke", "clock_skew")
CORRUPT_QUALITY = 0.0


@dataclass(frozen=True)
class Intervention:
    op: str
    target: Selector = field(default_factory=dict)
    params: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.op not in OPS:
            raise ValueError(f"unknown intervention op {self.op!r}; expected one of {OPS}")
        object.__setattr__(self, "target", dict(self.target or {}))
        object.__setattr__(self, "params", dict(self.params or {}))

    def to_dict(self) -> dict:
        return {"op": self.op, "target": dict(self.target), "params": dict(self.params)}

    @staticmethod
    def from_dict(d: dict) -> "Intervention":
        return Intervention(op=d["op"], target=dict(d.get("target") or {}), params=dict(d.get("params") or {}))

    def hash(self) -> str:
        return content_hash(self.to_dict())

    def __hash__(self) -> int:  # dict fields are not hashable; identity is the content hash
        return hash(self.hash())

    @property
    def is_concrete(self) -> bool:
        return "event_id" in self.target

    @property
    def role(self) -> str | None:
        """Role restriction, from target or params (either place is accepted)."""
        return self.target.get("role") or self.params.get("role")


FaultSchedule = tuple[Intervention, ...]


def schedule_hash(schedule: Iterable[Intervention]) -> str:
    return content_hash([iv.to_dict() for iv in schedule])


# ---------------------------------------------------------------- selectors

def _get_path(obj: Any, path: str) -> Any:
    """Read a dotted path such as 'payload.confidence' from an Event or dict. Missing -> None."""
    cur: Any = obj
    for part in path.split("."):
        if cur is None:
            return None
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            cur = getattr(cur, part, None)
    return cur


def _payload_matches(event: Event, match: dict) -> bool:
    for key, want in match.items():
        path = key if key.startswith("payload.") else f"payload.{key}"
        if _get_path(event, path) != want:
            return False
    return True


def resolve(selector: Selector, events: Sequence[Event]) -> list[str]:
    """Abstract selector -> concrete event ids, in canonical order.

    Keys: event_id (concrete), kind, source_id, payload_match {field: value}, nth (0-based index
    into the filtered canonical order; negative counts from the end). `role` is a delivery filter
    handled by the hook and is ignored here.
    """
    sel = selector or {}
    ordered = sorted(events, key=Event.sort_key)
    if "event_id" in sel:
        return [e.id for e in ordered if e.id == sel["event_id"]]
    out = []
    for e in ordered:
        if "kind" in sel and e.kind != sel["kind"]:
            continue
        if "source_id" in sel and e.source_id != sel["source_id"]:
            continue
        if sel.get("payload_match") and not _payload_matches(e, sel["payload_match"]):
            continue
        out.append(e.id)
    if "nth" in sel and sel["nth"] is not None:
        n = int(sel["nth"])
        if -len(out) <= n < len(out):
            return [out[n]]
        return []
    return out


# ---------------------------------------------------------------- the delivery hook

Resolver = Callable[[Selector], list[str]]
DeliveryHook = Callable[[Event, str, int, DeterministicRNG], list[tuple[Event, int | None]]]


def _make_resolver(resolver: Resolver | Sequence[Event] | None) -> tuple[Resolver, list[Event] | None]:
    if resolver is None:
        return (lambda sel: []), None
    if callable(resolver):
        return resolver, None
    events = list(resolver)
    return (lambda sel: resolve(sel, events)), events


def _next_same_kind(event: Event, events: Sequence[Event]) -> Event | None:
    later = [e for e in sorted(events, key=Event.sort_key)
             if e.kind == event.kind and e.id != event.id and e.sort_key() > event.sort_key()]
    return later[0] if later else None


def _set_path(event: Event, path: str, value: Any) -> Event:
    parts = path.split(".")
    if parts[0] == "payload":
        payload = dict(event.payload)
        if len(parts) == 1:
            raise ValueError("corrupt field must name a key inside payload, e.g. payload.confidence")
        cur = payload
        for p in parts[1:-1]:
            nxt = dict(cur.get(p) or {})
            cur[p] = nxt
            cur = nxt
        cur[parts[-1]] = value
        return event.replace(payload=payload)
    if len(parts) == 1 and parts[0] in Event.__dataclass_fields__:
        return event.replace(**{parts[0]: value})
    raise ValueError(f"unsupported corrupt field path {path!r}")


def hook_for_schedule(schedule: Iterable[Intervention],
                      resolver: Resolver | Sequence[Event] | None = None) -> DeliveryHook:
    """Build the delivery hook the worlds runner calls for every (truth event, role).

    `resolver` is either a callable selector -> list[event_id] over truth, or the list of truth
    events itself (then `resolve` is used). Concrete {"event_id"} targets need no resolver.
    Interventions apply in schedule order; each one sees the deliveries produced by the previous.
    """
    schedule = tuple(schedule)
    resolve_sel, truth_events = _make_resolver(resolver)
    cache: dict[str, frozenset[str]] = {}

    def resolved_ids(iv: Intervention) -> frozenset[str]:
        key = iv.hash()
        if key not in cache:
            sel = {k: v for k, v in iv.target.items() if k != "role"}
            cache[key] = frozenset(resolve_sel(sel))
        return cache[key]

    def targets(iv: Intervention, event: Event, role: str) -> bool:
        if iv.role is not None and iv.role != role:
            return False
        sel = {k: v for k, v in iv.target.items() if k != "role"}
        if not sel:
            return True  # untargeted: applies to every delivery (revoke, clock_skew)
        if "event_id" in sel:
            return event.id == sel["event_id"] or _origin_id(event) == sel["event_id"]
        ids = resolved_ids(iv)
        return event.id in ids or _origin_id(event) in ids

    def apply_one(iv: Intervention, event: Event, role: str, t: int | None,
                  rng: DeterministicRNG) -> list[tuple[Event, int | None]]:
        p = iv.params
        if t is None:  # already suppressed by an earlier intervention
            return [(event, None)]
        if iv.op == "clock_skew":
            if event.source_id != p.get("source_id"):
                return [(event, t)]
            return [(event, t + int(p.get("offset_ns", 0)))]
        if iv.op == "revoke":
            if iv.role is not None and iv.role != role:
                return [(event, t)]
            return [(event, None if t >= int(p.get("from_ns", 0)) else t)]
        if not targets(iv, event, role):
            return [(event, t)]
        if iv.op == "delay":
            return [(event, t + int(p.get("by_ns", 0)))]
        if iv.op == "drop":
            return [(event, None)]
        if iv.op == "duplicate":
            count = int(p.get("count", 1))
            spacing = int(p.get("spacing_ns", 0))
            origin = _origin_id(event) or event.id
            out = [(event, t)]
            for i in range(1, count + 1):
                dup = event.replace(id=f"{origin}#dup{i}",
                                    provenance_ids=tuple(dict.fromkeys((origin, *event.provenance_ids))))
                out.append((dup, t + i * spacing))
            return out
        if iv.op == "reorder":
            by = p.get("by_ns")
            if by is None:
                nxt = _next_same_kind(event, truth_events) if truth_events else None
                by = (nxt.event_time_ns - event.event_time_ns + int(p.get("margin_ns", 1))) if nxt else 0
            return [(event, t + int(by))]
        if iv.op == "corrupt":
            corrupted = _set_path(event, p["field"], p.get("value"))
            origin = _origin_id(event) or event.id
            corrupted = corrupted.replace(
                quality=float(p.get("quality", CORRUPT_QUALITY)),
                provenance_ids=tuple(dict.fromkeys((origin, *event.provenance_ids))))
            return [(corrupted, t)]
        raise ValueError(f"unhandled op {iv.op}")  # pragma: no cover

    def hook(event: Event, role: str, received_time_ns: int,
             rng: DeterministicRNG) -> list[tuple[Event, int | None]]:
        deliveries: list[tuple[Event, int | None]] = [(event, received_time_ns)]
        for iv in schedule:
            nxt: list[tuple[Event, int | None]] = []
            for ev, t in deliveries:
                nxt.extend(apply_one(iv, ev, role, t, rng))
            deliveries = nxt
        return deliveries

    return hook


def _origin_id(event: Event) -> str | None:
    """Original truth id for a duplicate/corrupted copy (first provenance id), else None."""
    if "#dup" in event.id:
        return event.id.split("#dup", 1)[0]
    if event.provenance_ids and event.id in event.provenance_ids:
        return event.id
    return None


def apply_at_delivery(schedule: Iterable[Intervention], event: Event, role: str, received_time_ns: int,
                      rng: DeterministicRNG, truth_events: Sequence[Event] | None = None,
                      ) -> list[tuple[Event, int | None]]:
    """One-shot convenience over `hook_for_schedule` (contract name `apply_at_delivery`)."""
    return hook_for_schedule(schedule, truth_events)(event, role, received_time_ns, rng)


def apply(schedule: Iterable[Intervention], truth_events: Sequence[Event],
          deliveries: Iterable[tuple[Event, str, int]], rng: DeterministicRNG,
          ) -> list[tuple[Event, str, int | None]]:
    """Apply a schedule to planned deliveries [(event, role, received_time_ns)] -> perturbed ones."""
    hook = hook_for_schedule(schedule, truth_events)
    out: list[tuple[Event, str, int | None]] = []
    for ev, role, t in deliveries:
        for ev2, t2 in hook(ev, role, t, rng):
            out.append((ev2, role, t2))
    return out
