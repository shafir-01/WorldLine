"""Three-valued invariant checker with witnesses.

Checkers duck-type the Trace: they read only `scenario_id, seed, branch_id, fault_schedule_hash,
truth (EventLog), views (dict[role, EventLog]), decisions (list[Event]), digest`. They never read
anything a role could not know except where the invariant is *about* truth (always, decision).

Verdicts:
  PASS     the property held on every applicable instance
  FAIL     a counterexample exists; `witness` is the minimal list of event ids showing it
  UNKNOWN  the trace lacks the signals needed to decide; `missing_data` names them
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable, Iterable

import yaml

from worldline.core import Event, Verdict

KINDS = ("always", "response", "absence", "epistemic", "decision")
RuleEvaluator = Callable[[str, list[Event]], Any]  # (rule_id, events_known) -> option


@dataclass
class Invariant:
    id: str
    version: str
    kind: str
    description: str = ""
    params: dict = field(default_factory=dict)
    required_signals: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"invariant {self.id}: unknown kind {self.kind!r}; expected one of {KINDS}")
        self.version = str(self.version)
        self.params = dict(self.params or {})
        self.required_signals = list(self.required_signals or [])

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class VerdictRecord:
    invariant_id: str
    invariant_version: str
    result: Verdict
    witness: list[str]
    explanation: str
    missing_data: list[str]
    derived_at_ns: int
    trace_digest: str

    def to_dict(self) -> dict:
        d = asdict(self)
        d["result"] = self.result.value
        return d


# ---------------------------------------------------------------- loading

def load_invariants(path: str | Path) -> dict[str, Invariant]:
    """Load invariants from one YAML file or every *.yaml/*.yml in a directory.

    File shape: {"invariants": [{id, version, kind, description, params, required_signals}, ...]}
    (a bare list is also accepted).
    """
    p = Path(path)
    files = sorted([*p.glob("*.yaml"), *p.glob("*.yml")]) if p.is_dir() else [p]
    out: dict[str, Invariant] = {}
    for f in files:
        data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        items = data.get("invariants", []) if isinstance(data, dict) else data
        for item in items:
            inv = Invariant(**item)
            if inv.id in out:
                raise ValueError(f"duplicate invariant id {inv.id!r} in {f}")
            out[inv.id] = inv
    return out


# ---------------------------------------------------------------- helpers over the trace

def _get_path(obj: Any, path: str) -> Any:
    cur: Any = obj
    for part in path.split("."):
        if cur is None:
            return None
        cur = cur.get(part) if isinstance(cur, dict) else getattr(cur, part, None)
    return cur


def _truth(trace) -> list[Event]:
    return list(trace.truth.ordered())


def _views(trace) -> dict[str, list[Event]]:
    return {r: sorted(v, key=lambda e: (e.received_time_ns if e.received_time_ns is not None else -1, *e.sort_key()))
            for r, v in (getattr(trace, "views", {}) or {}).items()}


def _decisions(trace) -> list[Event]:
    return sorted(getattr(trace, "decisions", []) or [], key=Event.sort_key)


def _present_kinds(trace) -> set[str]:
    kinds = {e.kind for e in _truth(trace)}
    for v in _views(trace).values():
        kinds |= {e.kind for e in v}
    kinds |= {e.kind for e in _decisions(trace)}
    return kinds


def _is_corrupted(e: Event) -> bool:
    """Corruption marker set by interventions.corrupt: quality 0.0 and/or self-citing provenance."""
    return (e.quality is not None and e.quality == 0.0) or (e.id in e.provenance_ids)


def _actor_role(trace, decision: Event, scenario, params: dict) -> str | None:
    pl = decision.payload or {}
    if pl.get("role"):
        return pl["role"]
    actor = pl.get("actor")
    for a in getattr(scenario, "actors", None) or []:
        if getattr(a, "id", None) == actor or (isinstance(a, dict) and a.get("id") == actor):
            return getattr(a, "role", None) if not isinstance(a, dict) else a.get("role")
    mapped = (params.get("actor_roles") or {}).get(actor)
    if mapped:
        return mapped
    views = getattr(trace, "views", {}) or {}
    if len(views) == 1:
        return next(iter(views))
    return None


def _known_at(view: list[Event], t_ns: int) -> list[Event]:
    return [e for e in view if e.received_time_ns is not None and e.received_time_ns <= t_ns]


def _record(inv: Invariant, trace, result: Verdict, witness: list[str], explanation: str,
            missing: list[str], now_ns: int) -> VerdictRecord:
    return VerdictRecord(invariant_id=inv.id, invariant_version=inv.version, result=result,
                         witness=list(witness), explanation=explanation, missing_data=list(missing),
                         derived_at_ns=now_ns, trace_digest=getattr(trace, "digest", ""))


# ---------------------------------------------------------------- kinds

def _check_always(trace, inv: Invariant) -> tuple[Verdict, list[str], str, list[str]]:
    p = inv.params
    fld = p.get("field", "")
    kind = p.get("kind")
    events = [e for e in _truth(trace) if (kind is None or e.kind == kind) and _get_path(e, fld) is not None]
    if not events:
        return Verdict.UNKNOWN, [], f"no truth event carries {fld!r}", [f"field:{fld}"]
    for e in events:
        v = _get_path(e, fld)
        bad = ((("min" in p) and v < p["min"]) or (("max" in p) and v > p["max"])
               or (("equals" in p) and v != p["equals"]) or (("not_equals" in p) and v == p["not_equals"]))
        if bad:
            return Verdict.FAIL, [e.id], f"{fld}={v!r} on {e.id} violates {_bounds(p)}", []
    return Verdict.PASS, [], f"{fld} satisfied {_bounds(p)} on {len(events)} truth events", []


def _bounds(p: dict) -> str:
    return ", ".join(f"{k}={p[k]!r}" for k in ("min", "max", "equals", "not_equals") if k in p) or "no bound"


def _check_response(trace, inv: Invariant) -> tuple[Verdict, list[str], str, list[str]]:
    p = inv.params
    role, trig, resp, within = p["role"], p["trigger_kind"], p["response_kind"], int(p["within_ns"])
    link = p.get("link_field", "acks")
    views = _views(trace)
    if role not in views:
        return Verdict.UNKNOWN, [], f"no view for role {role!r}", [f"view:{role}"]
    view = views[role]
    triggers = [e for e in view if e.kind == trig and e.received_time_ns is not None]
    if not triggers:
        return Verdict.UNKNOWN, [], f"role {role!r} received no {trig!r} events", [trig]
    for t in triggers:
        deadline = t.received_time_ns + within
        acked = any(e.kind == resp and e.received_time_ns is not None and e.received_time_ns <= deadline
                    and _get_path(e, f"payload.{link}") == t.id for e in view)
        if not acked:
            before = [e for e in view if e.id != t.id and e.received_time_ns is not None
                      and e.received_time_ns <= deadline]
            witness = [t.id] + ([before[-1].id] if before else [])
            return (Verdict.FAIL, witness,
                    f"{trig} {t.id} received by {role} at {t.received_time_ns} had no {resp} by {deadline}", [])
    return Verdict.PASS, [], f"all {len(triggers)} {trig} events acknowledged within {within}ns by {role}", []


def _match_pred(e: Event, pred: dict) -> bool:
    if "kind" in pred and e.kind != pred["kind"]:
        return False
    if "source_id" in pred and e.source_id != pred["source_id"]:
        return False
    if "label" in pred and pred["label"] not in e.policy_labels:
        return False
    if "quality" in pred and e.quality != pred["quality"]:
        return False
    if pred.get("corrupted") and not _is_corrupted(e):
        return False
    for k, v in (pred.get("payload") or {}).items():
        if _get_path(e, f"payload.{k}") != v:
            return False
    return True


def _check_absence(trace, inv: Invariant) -> tuple[Verdict, list[str], str, list[str]]:
    p = inv.params
    pred = p.get("match") or {}
    scope = p.get("scope", "views")
    role = p.get("role")
    if scope == "decisions":
        decisions = _decisions(trace)
        if not decisions:
            return Verdict.UNKNOWN, [], "trace has no decisions", ["decision"]
        views = _views(trace)
        by_id: dict[str, Event] = {}
        for v in views.values():
            for e in v:
                by_id.setdefault(e.id, e)
        for d in decisions:
            for kid in d.payload.get("known_event_ids", []):
                e = by_id.get(kid)
                if e is not None and _match_pred(e, pred):
                    return Verdict.FAIL, [d.id, kid], f"decision {d.id} relied on {kid} matching {pred}", []
        return Verdict.PASS, [], f"no decision relied on an event matching {pred}", []
    views = _views(trace)
    if role is not None and role not in views:
        return Verdict.UNKNOWN, [], f"no view for role {role!r}", [f"view:{role}"]
    scoped = {role: views[role]} if role is not None else views
    if not any(scoped.values()):
        return Verdict.UNKNOWN, [], "no view events to inspect", ["role_views"]
    bad = [e.id for v in scoped.values() for e in v if _match_pred(e, pred)]
    if bad:
        return Verdict.FAIL, bad, f"{len(bad)} event(s) matching {pred} present in view(s) {sorted(scoped)}", []
    return Verdict.PASS, [], f"no event matching {pred} in view(s) {sorted(scoped)}", []


def _check_epistemic(trace, inv: Invariant, scenario) -> tuple[Verdict, list[str], str, list[str]]:
    p = inv.params
    rule = p.get("rule", "no_future_knowledge")
    views = _views(trace)
    if rule == "label_isolation":
        role, label = p["role"], p["label"]
        if role not in views:
            return Verdict.UNKNOWN, [], f"no view for role {role!r}", [f"view:{role}"]
        bad = [e.id for e in sorted(views[role], key=Event.sort_key) if label in e.policy_labels]
        if bad:
            return Verdict.FAIL, bad, f"role {role!r} received {len(bad)} event(s) labelled {label!r}", []
        return Verdict.PASS, [], f"role {role!r} never received an event labelled {label!r}", []
    if rule != "no_future_knowledge":
        raise ValueError(f"unknown epistemic rule {rule!r}")
    decisions = _decisions(trace)
    if not any(views.values()) and not decisions:
        return Verdict.UNKNOWN, [], "trace has neither view events nor decisions", ["role_views"]
    witness: list[str] = []
    notes: list[str] = []
    for role in sorted(views):
        for e in sorted(views[role], key=Event.sort_key):
            if e.received_time_ns is None or e.received_time_ns < e.event_time_ns:
                witness.append(e.id)
                notes.append(f"{role} holds {e.id} received at {e.received_time_ns} before event_time {e.event_time_ns}")
    for d in decisions:
        at = int(d.payload.get("at_ns", d.event_time_ns))
        role = _actor_role(trace, d, scenario, p)
        pool = views.get(role, []) if role is not None else [e for v in views.values() for e in v]
        known = {e.id for e in _known_at(pool, at)}
        unknown = [k for k in d.payload.get("known_event_ids", []) if k not in known]
        if unknown:
            witness.append(d.id)
            witness.extend(unknown)
            notes.append(f"decision {d.id} at {at} cites {unknown} not received by role {role!r} by then")
    if witness:
        return Verdict.FAIL, witness, "; ".join(notes), []
    return Verdict.PASS, [], "no view or decision knows anything before it was received", []


def _check_decision(trace, inv: Invariant, scenario, evaluate_rule: RuleEvaluator | None,
                    ) -> tuple[Verdict, list[str], str, list[str]]:
    p = inv.params
    decisions = _decisions(trace)
    if p.get("rule_id"):
        decisions = [d for d in decisions if d.payload.get("rule_id") == p["rule_id"]]
    if not decisions:
        return Verdict.UNKNOWN, [], "trace has no decisions to compare", ["decision"]
    if evaluate_rule is None and scenario is not None and callable(getattr(scenario, "evaluate_rule", None)):
        evaluate_rule = scenario.evaluate_rule
    if evaluate_rule is None:
        return Verdict.UNKNOWN, [], "no decision-rule evaluator supplied", ["decision_rule_evaluator"]
    truth = _truth(trace)
    views = _views(trace)
    for d in decisions:
        at = int(d.payload.get("at_ns", d.event_time_ns))
        truth_as_of = [e for e in truth if e.event_time_ns <= at]
        expected = evaluate_rule(d.payload.get("rule_id"), truth_as_of)
        actual = d.payload.get("option")
        if expected == actual:
            continue
        role = _actor_role(trace, d, scenario, p)
        if role is not None and role in views:
            seen = {e.id for e in _known_at(views[role], at)}
        else:
            seen = set(d.payload.get("known_event_ids", []))
        missing = [e.id for e in truth_as_of if e.id not in seen]
        witness = missing or [d.id]
        return (Verdict.FAIL, witness,
                f"decision {d.id} ({d.payload.get('rule_id')}) chose {actual!r} but truth as of {at} "
                f"gives {expected!r}; actor had not received {missing}", [])
    return Verdict.PASS, [], f"all {len(decisions)} decision(s) match the rule evaluated over truth", []


# ---------------------------------------------------------------- entry points

def check(trace, inv: Invariant, *, scenario=None, evaluate_rule: RuleEvaluator | None = None,
          now_ns: int = 0) -> VerdictRecord:
    """Evaluate one invariant on a trace. `now_ns` is the evaluator's clock (derived_at_ns)."""
    missing = [k for k in inv.required_signals if k not in _present_kinds(trace)]
    if missing:
        return _record(inv, trace, Verdict.UNKNOWN, [], f"required signal kinds absent: {missing}", missing, now_ns)
    if inv.kind == "always":
        res = _check_always(trace, inv)
    elif inv.kind == "response":
        res = _check_response(trace, inv)
    elif inv.kind == "absence":
        res = _check_absence(trace, inv)
    elif inv.kind == "epistemic":
        res = _check_epistemic(trace, inv, scenario)
    elif inv.kind == "decision":
        res = _check_decision(trace, inv, scenario, evaluate_rule)
    else:  # pragma: no cover
        raise ValueError(inv.kind)
    result, witness, explanation, missing_data = res
    return _record(inv, trace, result, witness, explanation, missing_data, now_ns)


def check_all(trace, invariants: dict[str, Invariant] | Iterable[Invariant], **kw) -> list[VerdictRecord]:
    items = list(invariants.values()) if isinstance(invariants, dict) else list(invariants)
    return [check(trace, inv, **kw) for inv in sorted(items, key=lambda i: i.id)]
