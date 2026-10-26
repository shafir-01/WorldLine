"""Scenario grammar: the YAML shape that describes a simulated world.

A scenario is pure data. Nothing in it runs code: decision rules are strings in a
tiny predicate DSL (see ``worldline.worlds.predicate``), injects are the truth
events that will happen, transitions are state changes of the world itself
(an outage that silences a source), and roles/actors describe who observes what
through which delivery policy.

YAML grammar (see ``scenarios/port_alert.yaml`` for a full example)::

    id: port_alert
    seed: 42
    clock: {epoch_ns: 0, tick_ns: 1s, horizon_ns: 1h}
    roles:
      - id: harbour_ops
        visibility: ["*"]                 # policy labels this role may see; "*" = all
        delivery: {base_latency_ns: 2s, jitter_ns: 1s, drop_probability: 0.0, ordering: fifo}
    actors:
      - id: duty_officer
        role: harbour_ops
        decision_rules:
          - id: escalate_or_monitor
            at_ns: 1500s
            window_ns: 600s
            when: "count(kind='sighting', payload.confidence>=0.6, not_corrected) >= 2"
            then: escalate
            otherwise: monitor
    injects:
      - {id: alert-001, at_ns: 60s, source_id: harbour_vts, kind: alert,
         payload: {level: amber}, policy_labels: [public]}
    transitions:
      - {id: radar_outage, kind: outage, from_ns: 1800s, to_ns: 2100s, source_id: radar_north}
    invariants: [response.alert_acknowledged]

Every ``*_ns`` field accepts an integer (nanoseconds) or a string with a unit
suffix: ``ns``, ``us``, ``ms``, ``s``, ``m``, ``h``.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .predicate import parse_predicate

_UNITS = {"ns": 1, "us": 1_000, "ms": 1_000_000, "s": 1_000_000_000, "m": 60_000_000_000, "h": 3_600_000_000_000}
_DURATION = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*(ns|us|ms|s|m|h)\s*$")


def parse_ns(value: Any) -> int:
    """Accept ``1500000000000``, ``"1500s"``, ``"25m"`` ... and return integer nanoseconds."""
    if isinstance(value, bool):
        raise TypeError(f"not a duration: {value!r}")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not value.is_integer():
            raise ValueError(f"fractional nanoseconds are not allowed: {value!r}")
        return int(value)
    if isinstance(value, str):
        m = _DURATION.match(value)
        if m:
            qty, unit = m.groups()
            return int(round(float(qty) * _UNITS[unit]))
        if re.fullmatch(r"\s*-?\d+\s*", value):
            return int(value)
    raise ValueError(f"cannot parse duration: {value!r}")


@dataclass
class DeliveryPolicy:
    """Per role: how truth becomes observation."""

    base_latency_ns: int = 0
    jitter_ns: int = 0            # uniform in [0, jitter_ns], drawn from rng.fork(f"deliver/{role}/{event_id}")
    drop_probability: float = 0.0
    ordering: str = "fifo"        # "fifo" (received times monotone in truth order) | "network" (jitter may reorder)

    def __post_init__(self) -> None:
        if self.ordering not in ("fifo", "network"):
            raise ValueError(f"ordering must be 'fifo' or 'network', got {self.ordering!r}")
        if not (0.0 <= self.drop_probability <= 1.0):
            raise ValueError("drop_probability must be in [0, 1]")
        if self.base_latency_ns < 0 or self.jitter_ns < 0:
            raise ValueError("latency and jitter must be non-negative")


@dataclass
class Role:
    id: str
    delivery: DeliveryPolicy = field(default_factory=DeliveryPolicy)
    visibility: list[str] = field(default_factory=lambda: ["*"])

    def can_see(self, policy_labels: tuple[str, ...] | list[str]) -> bool:
        if "*" in self.visibility:
            return True
        return all(label in self.visibility for label in policy_labels)


@dataclass
class DecisionRule:
    """Deterministic policy, no LLM. ``when`` is a predicate DSL string."""

    id: str
    at_ns: int
    when: str
    then: str
    otherwise: str
    window_ns: int

    def __post_init__(self) -> None:
        parse_predicate(self.when)  # fail fast on a malformed rule
        if self.window_ns < 0:
            raise ValueError("window_ns must be non-negative")


@dataclass
class Actor:
    id: str
    role: str
    decision_rules: list[DecisionRule] = field(default_factory=list)


@dataclass
class Inject:
    """One truth event that will happen at ``at_ns``."""

    at_ns: int
    source_id: str
    kind: str
    payload: dict = field(default_factory=dict)
    policy_labels: list[str] = field(default_factory=list)
    geometry: dict | None = None
    id: str | None = None          # defaults to "<kind>-<ordinal>" at load time
    priority: int = 0


@dataclass
class TruthTransition:
    """A state change in the simulated world.

    kind="outage": events from ``source_id`` with event_time in [from_ns, to_ns]
    are never delivered to ``roles`` (all roles if empty). The events still exist
    in truth: the world happened, nobody heard about it.
    """

    id: str
    kind: str
    from_ns: int
    to_ns: int
    source_id: str | None = None
    roles: list[str] = field(default_factory=list)
    params: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.kind not in ("outage",):
            raise ValueError(f"unknown transition kind {self.kind!r}")
        if self.to_ns < self.from_ns:
            raise ValueError(f"transition {self.id}: to_ns < from_ns")

    def suppresses(self, source_id: str, event_time_ns: int, role_id: str) -> bool:
        if self.kind != "outage":
            return False
        if self.source_id is not None and self.source_id != source_id:
            return False
        if self.roles and role_id not in self.roles:
            return False
        return self.from_ns <= event_time_ns <= self.to_ns


@dataclass
class Scenario:
    id: str
    seed: int
    clock: dict                         # {epoch_ns, tick_ns, horizon_ns}
    roles: list[Role] = field(default_factory=list)
    actors: list[Actor] = field(default_factory=list)
    injects: list[Inject] = field(default_factory=list)
    transitions: list[TruthTransition] = field(default_factory=list)
    invariants: list[str] = field(default_factory=list)

    # -- lookups ----------------------------------------------------------
    @property
    def horizon_ns(self) -> int:
        return int(self.clock["horizon_ns"])

    def role(self, role_id: str) -> Role:
        for r in self.roles:
            if r.id == role_id:
                return r
        raise KeyError(f"unknown role {role_id!r}")

    def validate(self) -> None:
        role_ids = [r.id for r in self.roles]
        if len(set(role_ids)) != len(role_ids):
            raise ValueError("duplicate role ids")
        for a in self.actors:
            if a.role not in role_ids:
                raise ValueError(f"actor {a.id!r} refers to unknown role {a.role!r}")
            for rule in a.decision_rules:
                if rule.at_ns > self.horizon_ns:
                    raise ValueError(f"rule {rule.id!r} decides after the horizon")
        ids = [i.id for i in self.injects]
        if len(set(ids)) != len(ids):
            dup = sorted({i for i in ids if ids.count(i) > 1})
            raise ValueError(f"duplicate inject ids: {dup}")
        for k in ("epoch_ns", "tick_ns", "horizon_ns"):
            if k not in self.clock:
                raise ValueError(f"clock missing {k!r}")

    # -- serialization ----------------------------------------------------
    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "Scenario":
        return scenario_from_dict(d)


def _ns_fields(d: dict, keys: tuple[str, ...]) -> dict:
    out = dict(d)
    for k in keys:
        if k in out and out[k] is not None:
            out[k] = parse_ns(out[k])
    return out


def scenario_from_dict(d: dict) -> Scenario:
    """Build and validate a Scenario from a plain dict (the YAML shape)."""
    if not isinstance(d, dict):
        raise ValueError("scenario must be a mapping")
    for k in ("id", "seed", "clock"):
        if k not in d:
            raise ValueError(f"scenario missing required key {k!r}")
    clock = _ns_fields(dict(d["clock"]), ("epoch_ns", "tick_ns", "horizon_ns"))
    clock.setdefault("epoch_ns", 0)
    clock.setdefault("tick_ns", 1_000_000_000)

    roles = []
    for r in d.get("roles") or []:
        pol = _ns_fields(dict(r.get("delivery") or {}), ("base_latency_ns", "jitter_ns"))
        roles.append(Role(id=str(r["id"]), delivery=DeliveryPolicy(**pol),
                          visibility=list(r.get("visibility") or ["*"])))

    actors = []
    for a in d.get("actors") or []:
        rules = [DecisionRule(**_ns_fields(dict(rd), ("at_ns", "window_ns"))) for rd in a.get("decision_rules") or []]
        actors.append(Actor(id=str(a["id"]), role=str(a["role"]), decision_rules=rules))

    injects = []
    counters: dict[str, int] = {}
    for inj in d.get("injects") or []:
        inj = _ns_fields(dict(inj), ("at_ns",))
        kind = str(inj["kind"])
        counters[kind] = counters.get(kind, 0) + 1
        injects.append(Inject(
            at_ns=inj["at_ns"], source_id=str(inj["source_id"]), kind=kind,
            payload=dict(inj.get("payload") or {}),
            policy_labels=[str(x) for x in (inj.get("policy_labels") or [])],
            geometry=inj.get("geometry"),
            id=str(inj["id"]) if inj.get("id") is not None else f"{kind}-{counters[kind]:03d}",
            priority=int(inj.get("priority", 0)),
        ))

    transitions = [TruthTransition(**_ns_fields(dict(t), ("from_ns", "to_ns"))) for t in d.get("transitions") or []]

    sc = Scenario(id=str(d["id"]), seed=int(d["seed"]), clock=clock, roles=roles, actors=actors,
                  injects=injects, transitions=transitions, invariants=list(d.get("invariants") or []))
    sc.validate()
    return sc


def load_scenario(path: str | Path) -> Scenario:
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return scenario_from_dict(data)
