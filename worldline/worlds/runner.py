"""Deterministic runner: scenario -> Trace, plus snapshots and branches.

Everything here is a pure function of ``(scenario, seed, fault_schedule)``.
The only randomness is ``DeterministicRNG(seed).fork(f"deliver/{role}/{event_id}")``,
consumed once per (role, truth event) for the delivery jitter and the drop draw.

Information-state separation
----------------------------
``truth`` is what happened. ``views[role]`` is what that role received, each event
carrying ``received_time_ns``. Decision rules are evaluated *only* over
``view_as_known_at(trace, role, at_ns)``; the runner never hands truth to a rule.

Delivery hook
-------------
``run`` accepts ``delivery_hook(event, role_id, received_time_ns, rng)`` which
returns a list of ``(event, received_time_ns | None)`` pairs: ``None`` means the
delivery is dropped, several pairs mean duplication, a changed event means
corruption (the hook must then set ``provenance_ids`` to the original id so the
view stays auditable), a changed time means delay/reorder. The default hook is
the identity. ``worldline.interventions`` supplies the real hook from a
``FaultSchedule`` via ``hook_for_schedule(schedule)``; this module never imports
it at import time. The hook is given ``rng.fork("hook")`` of the per-delivery
stream so its randomness is also reproducible and does not disturb the jitter.

Contract for hooks: a returned ``received_time_ns`` must be >= the (possibly
modified) ``event.event_time_ns``; deliveries after the horizon are discarded.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Sequence

from worldline.core import DeterministicRNG, Event, EventLog, VirtualClock, content_hash

from .predicate import Context, parse_predicate
from .scenario import Scenario, scenario_from_dict

DeliveryHook = Callable[[Event, str, int, DeterministicRNG], list[tuple[Event, int | None]]]


def identity_hook(event: Event, role: str, received_time_ns: int, rng: DeterministicRNG) -> list[tuple[Event, int | None]]:
    return [(event, received_time_ns)]


def schedule_hash(schedule: Sequence[Any]) -> str:
    """Hash of a FaultSchedule. Delegates to worldline.interventions when present,
    otherwise hashes each intervention's ``hash()`` (or the object itself)."""
    schedule = tuple(schedule or ())
    if not schedule:
        return content_hash([])
    try:
        from worldline.interventions import schedule_hash as _sh  # type: ignore
        return _sh(schedule)
    except Exception:
        items = []
        for it in schedule:
            h = getattr(it, "hash", None)
            items.append(h() if callable(h) else it)
        return content_hash(items)


def _received_order(ev: Event) -> tuple:
    return (ev.received_time_ns if ev.received_time_ns is not None else -1,) + ev.sort_key()


# --- Trace -------------------------------------------------------------------

@dataclass
class Trace:
    scenario_id: str
    seed: int
    branch_id: str
    fault_schedule_hash: str
    truth: EventLog
    views: dict[str, EventLog]
    decisions: list[Event]
    clock_end: dict
    digest: str = ""
    scenario: Scenario | None = None   # carried so snapshots can be branched without re-loading

    def compute_digest(self) -> str:
        return content_hash({
            "scenario_id": self.scenario_id,
            "seed": self.seed,
            "fault_schedule_hash": self.fault_schedule_hash,
            "truth": self.truth.digest,
            "views": sorted([role, log.digest] for role, log in self.views.items()),
            "decisions": [d.content_hash for d in self.decisions],
        })

    def to_dict(self) -> dict:
        return {
            "scenario_id": self.scenario_id,
            "seed": self.seed,
            "branch_id": self.branch_id,
            "fault_schedule_hash": self.fault_schedule_hash,
            "truth": self.truth.to_dict(),
            "views": {r: v.to_dict() for r, v in sorted(self.views.items())},
            "decisions": [d.body() for d in self.decisions],
            "clock_end": dict(self.clock_end),
            "digest": self.digest,
            "scenario": self.scenario.to_dict() if self.scenario is not None else None,
        }

    @staticmethod
    def from_dict(d: dict) -> "Trace":
        t = Trace(
            scenario_id=d["scenario_id"], seed=int(d["seed"]), branch_id=d["branch_id"],
            fault_schedule_hash=d["fault_schedule_hash"],
            truth=EventLog.from_dict(d["truth"]),
            views={r: EventLog.from_dict(v) for r, v in d["views"].items()},
            decisions=[Event.from_dict(x) for x in d["decisions"]],
            clock_end=dict(d["clock_end"]),
            scenario=scenario_from_dict(d["scenario"]) if d.get("scenario") else None,
        )
        t.digest = t.compute_digest()
        if d.get("digest") and d["digest"] != t.digest:
            raise ValueError("trace digest mismatch: trace was altered")
        return t


# --- views -------------------------------------------------------------------

def view_as_known_at(trace: Trace, role: str, t_ns: int) -> list[Event]:
    """AS_KNOWN_AT: everything the role had received by t_ns, in received order."""
    log = trace.views[role]
    out = [e for e in log if e.received_time_ns is not None and e.received_time_ns <= t_ns]
    out.sort(key=_received_order)
    return out


def view_as_observed_at(trace: Trace, role: str, t_ns: int) -> list[Event]:
    """AS_OBSERVED_AT: events that had happened by t_ns and had been received by t_ns."""
    return [e for e in view_as_known_at(trace, role, t_ns) if e.event_time_ns <= t_ns]


# --- Snapshot ----------------------------------------------------------------

@dataclass
class Snapshot:
    """Everything up to t_ns, frozen: clock, truth so far, views so far, decisions so far, rng path."""

    scenario: Scenario
    t_ns: int
    branch_id: str
    fault_schedule_hash: str
    clock: dict
    truth: EventLog
    views: dict[str, EventLog]
    decisions: list[Event]
    rng: dict
    digest: str = ""

    def compute_digest(self) -> str:
        return content_hash({
            "scenario_id": self.scenario.id, "t_ns": self.t_ns, "branch_id": self.branch_id,
            "fault_schedule_hash": self.fault_schedule_hash, "clock": self.clock,
            "truth": self.truth.digest,
            "views": sorted([r, v.digest] for r, v in self.views.items()),
            "decisions": [d.content_hash for d in self.decisions], "rng": self.rng,
        })

    def to_dict(self) -> dict:
        return {
            "scenario": self.scenario.to_dict(), "t_ns": self.t_ns, "branch_id": self.branch_id,
            "fault_schedule_hash": self.fault_schedule_hash, "clock": dict(self.clock),
            "truth": self.truth.to_dict(), "views": {r: v.to_dict() for r, v in sorted(self.views.items())},
            "decisions": [d.body() for d in self.decisions], "rng": dict(self.rng), "digest": self.digest,
        }

    @staticmethod
    def from_dict(d: dict) -> "Snapshot":
        s = Snapshot(
            scenario=scenario_from_dict(d["scenario"]), t_ns=int(d["t_ns"]), branch_id=d["branch_id"],
            fault_schedule_hash=d["fault_schedule_hash"], clock=dict(d["clock"]),
            truth=EventLog.from_dict(d["truth"]), views={r: EventLog.from_dict(v) for r, v in d["views"].items()},
            decisions=[Event.from_dict(x) for x in d["decisions"]], rng=dict(d["rng"]),
        )
        s.digest = s.compute_digest()
        if d.get("digest") and d["digest"] != s.digest:
            raise ValueError("snapshot digest mismatch")
        return s


def snapshot_at(trace: Trace, t_ns: int) -> Snapshot:
    if trace.scenario is None:
        raise ValueError("trace carries no scenario; cannot snapshot")
    truth = EventLog()
    for e in trace.truth.ordered():
        if e.event_time_ns <= t_ns:
            truth.append(e)
    views: dict[str, EventLog] = {}
    for role, log in trace.views.items():
        v = EventLog()
        for e in sorted((e for e in log if e.received_time_ns is not None and e.received_time_ns <= t_ns), key=_received_order):
            v.append(e)
        views[role] = v
    decisions = [d for d in trace.decisions if d.event_time_ns <= t_ns]
    clock = VirtualClock(epoch_ns=trace.scenario.clock["epoch_ns"], tick_ns=trace.scenario.clock["tick_ns"])
    clock.advance_to(max(t_ns, clock.now_ns))
    snap = Snapshot(
        scenario=trace.scenario, t_ns=t_ns, branch_id=trace.branch_id,
        fault_schedule_hash=trace.fault_schedule_hash, clock=clock.snapshot(),
        truth=truth, views=views, decisions=decisions,
        rng=DeterministicRNG(trace.seed).describe(),
    )
    snap.digest = snap.compute_digest()
    return snap


def branch(snapshot: Snapshot, schedule: Sequence[Any] = (), *, delivery_hook: DeliveryHook | None = None) -> Trace:
    """Replay identically up to snapshot.t_ns, then diverge under ``schedule``.

    The frozen prefix is taken verbatim; only deliveries not yet received by t_ns
    and decisions after t_ns are (re)computed. A branch cannot change the past:
    anything the schedule would deliver before t_ns is clamped to t_ns + 1.
    """
    return run(snapshot.scenario, schedule, snapshot=snapshot, delivery_hook=delivery_hook)


# --- run ---------------------------------------------------------------------

def _truth_events(scenario: Scenario) -> list[Event]:
    evs = []
    for inj in scenario.injects:
        if inj.at_ns > scenario.horizon_ns:
            continue
        evs.append(Event(
            id=inj.id or inj.kind, scenario_id=scenario.id, source_id=inj.source_id,
            event_time_ns=inj.at_ns, kind=inj.kind, payload=dict(inj.payload),
            geometry=inj.geometry, policy_labels=tuple(inj.policy_labels), priority=inj.priority,
        ))
    return sorted(evs, key=Event.sort_key)


def _deliver_role(scenario: Scenario, role_id: str, truth: list[Event], root_rng: DeterministicRNG,
                  hook: DeliveryHook, frozen: EventLog | None, t_frozen: int | None) -> EventLog:
    role = scenario.role(role_id)
    pol = role.delivery
    horizon = scenario.horizon_ns
    already = {e.id for e in frozen} if frozen is not None else set()
    last_rt: int | None = None
    pending: list[Event] = []

    for ev in truth:  # canonical truth order
        if not role.can_see(ev.policy_labels):
            continue
        if any(t.suppresses(ev.source_id, ev.event_time_ns, role_id) for t in scenario.transitions):
            continue
        rng = root_rng.fork(f"deliver/{role_id}/{ev.id}")
        u = rng.random()
        jitter = rng.randint(0, pol.jitter_ns) if pol.jitter_ns > 0 else 0
        if pol.drop_probability > 0 and u < pol.drop_probability:
            continue
        rt = ev.event_time_ns + pol.base_latency_ns + jitter
        if pol.ordering == "fifo" and last_rt is not None and rt < last_rt:
            rt = last_rt
        last_rt = rt
        if ev.id in already:
            continue  # received in the frozen prefix
        for ev2, rt2 in hook(ev, role_id, rt, rng.fork("hook")):
            if rt2 is None:
                continue
            if t_frozen is not None and rt2 <= t_frozen:
                rt2 = t_frozen + 1
            if rt2 > horizon:
                continue
            if rt2 < ev2.event_time_ns:
                raise ValueError(
                    f"delivery_hook produced received_time_ns {rt2} < event_time_ns {ev2.event_time_ns} "
                    f"for event {ev2.id!r} to role {role_id!r}")
            if ev2.scenario_id != scenario.id:
                raise ValueError("delivery_hook changed scenario_id")
            pending.append(ev2.with_receipt(rt2))

    log = EventLog()
    if frozen is not None:
        for e in frozen:
            log.append(e)
    for e in sorted(pending, key=_received_order):
        log.append(e)
    return log


def _decide(trace: Trace, scenario: Scenario, frozen: list[Event], t_frozen: int | None) -> list[Event]:
    frozen_ids = {d.id for d in frozen}
    todo = []
    for actor in scenario.actors:
        for rule in actor.decision_rules:
            todo.append((rule.at_ns, actor.id, rule.id, actor, rule))
    todo.sort(key=lambda x: x[:3])
    out = list(frozen)
    for at_ns, actor_id, rule_id, actor, rule in todo:
        did = f"decision:{actor_id}:{rule_id}"
        if did in frozen_ids or (t_frozen is not None and at_ns <= t_frozen):
            continue
        known = view_as_known_at(trace, actor.role, at_ns)       # the ONLY source of information
        windowed = [e for e in known if e.received_time_ns >= at_ns - rule.window_ns]
        value = parse_predicate(rule.when).evaluate(known, windowed)
        option = rule.then if value else rule.otherwise
        out.append(Event(
            id=did, scenario_id=scenario.id, source_id=actor_id, event_time_ns=at_ns, kind="decision",
            payload={
                "actor": actor_id, "rule_id": rule_id, "option": option,
                "known_event_ids": [e.id for e in known], "at_ns": at_ns, "predicate_value": value,
            },
            received_time_ns=at_ns,
        ))
    out.sort(key=lambda d: (d.event_time_ns, d.payload["actor"], d.payload["rule_id"]))
    return out


def run(scenario: Scenario, schedule: Sequence[Any] = (), *, snapshot: Snapshot | None = None,
        delivery_hook: DeliveryHook | None = None) -> Trace:
    """Run a scenario deterministically and return its Trace.

    ``schedule`` is a FaultSchedule (tuple of interventions). When it is non-empty
    and no ``delivery_hook`` is given, the hook is obtained lazily from
    ``worldline.interventions.hook_for_schedule(schedule)``; if that module is not
    available an ImportError is raised. ``delivery_hook`` overrides this (used by
    tests and by interventions itself). See the module docstring for the hook contract.
    """
    schedule = tuple(schedule or ())
    if delivery_hook is None:
        if schedule:
            try:
                from worldline.interventions import hook_for_schedule  # noqa: WPS433 (lazy by design)
            except ImportError as exc:
                raise ImportError(
                    "run() was given a non-empty fault schedule but worldline.interventions "
                    "(hook_for_schedule) is not available; pass delivery_hook= explicitly or install the "
                    "interventions module") from exc
            delivery_hook = hook_for_schedule(schedule)
        else:
            delivery_hook = identity_hook

    if snapshot is not None and snapshot.scenario.id != scenario.id:
        raise ValueError("snapshot belongs to a different scenario")

    fs_hash = schedule_hash(schedule)
    if snapshot is None:
        branch_id = "root" if not schedule else f"root/{fs_hash}"
        clock = VirtualClock(epoch_ns=scenario.clock["epoch_ns"], tick_ns=scenario.clock["tick_ns"])
        t_frozen = None
    else:
        branch_id = snapshot.branch_id if not schedule else f"{snapshot.branch_id}/{fs_hash}"
        clock = VirtualClock.restore(snapshot.clock)
        t_frozen = snapshot.t_ns

    root_rng = DeterministicRNG(scenario.seed)
    truth_events = _truth_events(scenario)
    truth = EventLog()
    for e in truth_events:
        if e.event_time_ns > clock.now_ns:
            clock.advance_to(e.event_time_ns)
        truth.append(e)
    if snapshot is not None:
        # truth is a pure function of the scenario; the frozen prefix must agree
        prefix = EventLog()
        for e in truth_events:
            if e.event_time_ns <= t_frozen:
                prefix.append(e)
        if prefix.digest != snapshot.truth.digest:
            raise ValueError("snapshot truth prefix does not match the scenario")

    views: dict[str, EventLog] = {}
    for role in scenario.roles:
        frozen_view = snapshot.views.get(role.id) if snapshot is not None else None
        views[role.id] = _deliver_role(scenario, role.id, truth_events, root_rng, delivery_hook, frozen_view, t_frozen)

    clock.advance_to(max(clock.now_ns, scenario.horizon_ns))
    trace = Trace(
        scenario_id=scenario.id, seed=scenario.seed, branch_id=branch_id, fault_schedule_hash=fs_hash,
        truth=truth, views=views, decisions=[], clock_end=clock.snapshot(), scenario=scenario,
    )
    trace.decisions = _decide(trace, scenario, snapshot.decisions if snapshot is not None else [], t_frozen)
    trace.digest = trace.compute_digest()
    return trace
