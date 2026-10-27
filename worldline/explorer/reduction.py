"""Equivalence-based reduction, decision-window analysis and delay flip thresholds.

Delivery model
--------------
The runner computes, for every (role, truth event) that survives visibility, suppression and the
seeded drop draw, a *pre-hook* receipt time (base latency + jitter, FIFO-clamped). That time never
depends on the fault schedule: the schedule only enters through the delivery hook, which sees
``(event, role, pre_hook_rt)`` and returns the deliveries. ``DeliveryModel`` records those
pre-hook triples once, by running the root world with a recording hook (so the runner's own code
computes them, including deliveries later cut by the horizon), and then predicts any branch's views
by applying ``hook_for_schedule(schedule)`` to the recorded triples, with the runner's horizon cut
and time-rule checks. No RNG streams are re-derived, no decision is evaluated, no trace is hashed.

Signature
---------
``schedule_signature`` projects the predicted views onto what the invariant checker can observe:

* per decision rule (actor role R, at_ns, window_ns): the deliveries to R received by at_ns whose
  kind the rule's predicate can read, in receipt order, each as (id, content hash, inside window,
  exact receipt time if the predicate reads ``received_time_ns``);
* per invariant: the inputs its checker reads that the schedule can change (``invariant_component``;
  e.g. absence over decisions: the corrupted ids inside each decision's knowledge; latency: which
  deliveries exceed the bound; ordering: which causal links arrive before their cause);
* the set of kinds present in views (``required_signals``).

Two schedules with equal signatures produce equal verdict *results* for every invariant (witness
lists and trace digests may differ). An invariant kind, rule or scope the projection does not know
contributes the full predicted views ("exact" mode: sound, but only identical worlds merge). A
schedule the runner would reject yields ``None``; callers run it instead of skipping it.
See docs/EXPLORER.md.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from worldline.core import DeterministicRNG, Event, content_hash
from worldline.interventions import Intervention, hook_for_schedule
from worldline.interventions.mutators import _corrupt_value
from worldline.invariants import Invariant
from worldline.invariants.checker import _match_filter, _match_pred, _origin
from worldline.worlds import Scenario, Trace, run
from worldline.worlds.predicate import BoolOp, Call, Compare, Not, parse_predicate

_DUMMY_RNG = DeterministicRNG(0, ("reduction", "hook"))  # the hook ops never draw from it


# ------------------------------------------------------------------ predicate analysis

def _calls(node: Any) -> Iterable[Call]:
    if isinstance(node, Compare):
        yield node.call
    elif isinstance(node, Call):
        yield node
    elif isinstance(node, Not):
        yield from _calls(node.inner)
    elif isinstance(node, BoolOp):
        for i in node.items:
            yield from _calls(i)


def rule_kinds(when: str) -> frozenset[str] | None:
    """Every event kind a predicate can read, or None when it can read any kind.

    Stricter than ``decision_dependencies``: a call without a ``kind == '...'`` filter reads every
    kind, so the answer is None rather than the empty set.
    """
    out: set[str] = set()
    for call in _calls(parse_predicate(when).ast):
        kinds = {f.value for f in call.filters if f.field == "kind" and f.op == "==" and isinstance(f.value, str)}
        if not kinds:
            return None
        out |= kinds
        if call.flags:
            out.add("correction")
        if call.name == "acknowledged":
            out.add(str(call.kwargs.get("ack_kind", "ack")))
    return frozenset(out)


def rule_reads_receipt_time(when: str) -> bool:
    return any(f.field == "received_time_ns" for c in _calls(parse_predicate(when).ast) for f in c.filters)


def rule_receipt_time_literals(when: str) -> set[int]:
    """Numeric literals compared against received_time_ns (extra delay breakpoints)."""
    return {int(f.value) for c in _calls(parse_predicate(when).ast) for f in c.filters
            if f.field == "received_time_ns" and isinstance(f.value, (int, float)) and not isinstance(f.value, bool)}


def rule_payload_fields(when: str) -> set[str]:
    """payload.* fields a predicate filters on, plus the cross-reference fields its flags/acks read."""
    out: set[str] = set()
    for call in _calls(parse_predicate(when).ast):
        out |= {f.field.split(".", 1)[1] for f in call.filters if f.field.startswith("payload.")}
        if call.flags:
            out.add("corrects")
        if call.name == "acknowledged":
            out.add("acknowledges")
    return out


@dataclass(frozen=True)
class RuleInfo:
    actor: str
    rule_id: str
    role: str
    at_ns: int
    window_ns: int
    when: str
    then: str
    otherwise: str
    kinds: frozenset[str] | None
    time_sensitive: bool

    @property
    def decision_id(self) -> str:
        return f"decision:{self.actor}:{self.rule_id}"


def scenario_rules(scenario: Scenario) -> list[RuleInfo]:
    out = []
    for actor in scenario.actors:
        for r in actor.decision_rules:
            out.append(RuleInfo(actor.id, r.id, actor.role, int(r.at_ns), int(r.window_ns), r.when, r.then,
                                r.otherwise, rule_kinds(r.when), rule_reads_receipt_time(r.when)))
    return out


# ------------------------------------------------------------------ delivery model

Delivery = tuple[Event, int]  # (event as delivered, received_time_ns)


def _recv_key(d: Delivery) -> tuple:
    return (d[1],) + d[0].sort_key()


@dataclass
class DeliveryModel:
    scenario: Scenario
    root: Trace
    base: list[tuple[str, Event, int]]          # (role, truth event, pre-hook rt) in runner call order
    rules: list[RuleInfo] = field(default_factory=list)

    def root_receipt(self, role: str, event_id: str) -> int | None:
        for r, ev, rt in self.base:
            if r == role and ev.id == event_id:
                return rt
        return None

    def predict(self, schedule: Sequence[Intervention]) -> dict[str, list[Delivery]] | None:
        """Per-role deliveries in receipt order, or None if the runner would reject the schedule."""
        schedule = tuple(schedule)
        hook = hook_for_schedule(schedule) if schedule else None
        horizon = self.scenario.horizon_ns
        views: dict[str, list[Delivery]] = {r.id: [] for r in self.scenario.roles}
        for role, ev, rt in self.base:
            outs = hook(ev, role, rt, _DUMMY_RNG) if hook is not None else [(ev, rt)]
            for ev2, rt2 in outs:
                if rt2 is None or rt2 > horizon:
                    continue
                if rt2 < ev2.event_time_ns or ev2.scenario_id != self.scenario.id:
                    return None  # the runner raises ValueError: explore records it as rejected
                views[role].append((ev2, rt2))
        for v in views.values():
            v.sort(key=_recv_key)
        return views

    def decide(self, views: dict[str, list[Delivery]]) -> dict[str, str]:
        """decision id -> option, computed exactly as the runner's _decide does."""
        out = {}
        for r in self.rules:
            known = [ev.with_receipt(rt) for ev, rt in views.get(r.role, []) if rt <= r.at_ns]
            windowed = [e for e in known if e.received_time_ns >= r.at_ns - r.window_ns]
            out[r.decision_id] = r.then if parse_predicate(r.when).evaluate(known, windowed) else r.otherwise
        return out

    def predict_decisions(self, schedule: Sequence[Intervention]) -> dict[str, str] | None:
        views = self.predict(schedule)
        return None if views is None else self.decide(views)


def build_model(scenario: Scenario, root: Trace | None = None) -> DeliveryModel:
    """Record every pre-hook delivery triple by running the root world with a recording hook."""
    base: list[tuple[str, Event, int]] = []

    def record(event: Event, role: str, rt: int, rng: DeterministicRNG):
        base.append((role, event, rt))
        return [(event, rt)]

    recorded = run(scenario, (), delivery_hook=record)
    if root is None:
        root = run(scenario)
    if recorded.digest != root.digest:  # the recording hook is the identity; anything else is a bug
        raise RuntimeError("delivery model does not reproduce the root trace")
    return DeliveryModel(scenario, root, base, scenario_rules(scenario))


# ------------------------------------------------------------------ signature

def _known(views: dict[str, list[Delivery]], r: RuleInfo) -> list[Delivery]:
    """The rule's knowledge at the decision, in receipt order (= the decision's known_event_ids)."""
    out = []
    for ev, rt in views.get(r.role, []):
        if rt > r.at_ns:
            break
        out.append((ev, rt))
    return out


def _first_by_id(deliveries: Iterable[Delivery]) -> dict[str, Delivery]:
    """id -> first delivery with that id in receipt order (the checker's _event_index for a role)."""
    idx: dict[str, Delivery] = {}
    for d in deliveries:
        idx.setdefault(d[0].id, d)
    return idx


def _scoped_rules(model: DeliveryModel, p: dict) -> list[RuleInfo]:
    return [r for r in model.rules if not p.get("rule_id") or r.rule_id == p["rule_id"]]


def _exact_views(views: dict[str, list[Delivery]]) -> list:
    return [[role, [(ev.id, rt, ev.content_hash) for ev, rt in v]] for role, v in views.items()]


def _decision_known_component(model, views, p, fn) -> list:
    """[(decision id, fn(rule, known deliveries, id->event index over the rule's role view))]."""
    out = []
    for r in _scoped_rules(model, p):
        known = _known(views, r)
        out.append([r.decision_id, fn(r, known, _first_by_id(views.get(r.role, [])))])
    return out


def invariant_component(model: DeliveryModel, inv: Invariant, views: dict[str, list[Delivery]]) -> tuple[str, Any]:
    """(mode, component) for one invariant. mode "projected": the checker's inputs it can change;
    mode "exact": the full predicted views (any unknown kind, rule or scope; always sound)."""
    p = inv.params
    k = inv.kind
    if k in ("always", "decision"):
        # truth-only, or a function of the decision options (determined by the rule components)
        return "projected", None
    if k == "response":
        kinds = (p["trigger_kind"], p["response_kind"])
        link = p.get("link_field", "acks")
        view = views.get(p["role"])
        return "projected", None if view is None else [(ev.id, ev.kind, rt, (ev.payload or {}).get(link))
                                                         for ev, rt in view if ev.kind in kinds]
    if k == "latency":
        view = views.get(p["role"])
        bound = int(p["bound_ns"])
        return "projected", None if view is None else sorted(
            (ev.id, rt - ev.event_time_ns > bound) for ev, rt in view if ev.kind == p["kind"])
    if k == "ordering":
        fields = list(p.get("link_fields") or ["corrects", "acks", "acknowledges", "in_reply_to"])
        truth_ids = {e.id for e in model.root.truth}
        roles = [p["role"]] if p.get("role") is not None else sorted(views)
        comp = []
        for role in roles:
            if role not in views:
                comp.append([role, None])
                continue
            first: dict[str, int] = {}
            for ev, rt in views[role]:
                for key in (ev.id, _origin(ev, use_provenance=False)):
                    first.setdefault(key, rt)
            links = []
            for ev, rt in views[role]:
                for f in fields:
                    a = (ev.payload or {}).get(f)
                    if isinstance(a, str) and a in truth_ids:
                        links.append((ev.id, f, a, first.get(a) is not None and first[a] <= rt))
            comp.append([role, sorted(links)])
        return "projected", comp
    if k == "duplicate":
        flt = p.get("match") or {}
        use_prov = bool(p.get("use_provenance", True))

        def dups(r, known, idx):
            groups: dict[str, list[str]] = {}
            for ev, _ in known:
                e = idx[ev.id][0]
                if flt and not _match_filter(e, flt):
                    continue
                groups.setdefault(_origin(e, use_prov), []).append(ev.id)
            return sorted((o, sorted(ids)) for o, ids in groups.items() if len(ids) > 1)
        return "projected", _decision_known_component(model, views, p, dups)
    if k == "quorum":
        flt = p.get("match") or {}

        def sources(r, known, idx):
            evs = [idx[ev.id] for ev, _ in known]
            if p.get("window_ns") is not None:
                evs = [(e, rt) for e, rt in evs if rt >= r.at_ns - int(p["window_ns"])]
            corrected = ({e.payload.get("corrects") for e, _ in evs if e.kind == "correction"}
                         if p.get("exclude_corrected") else set())
            return sorted({e.source_id for e, _ in evs if _match_filter(e, flt) and _origin(e, False) not in corrected})
        return "projected", _decision_known_component(model, views, p, sources)
    if k == "absence" and p.get("scope", "views") == "decisions":
        pred = p.get("match") or {}
        by_id: dict[str, Event] = {}
        for v in views.values():  # the checker's lookup: role order of the trace, then receipt order
            for ev, _ in v:
                by_id.setdefault(ev.id, ev)
        return "projected", [[r.decision_id, sorted({ev.id for ev, _ in _known(views, r)
                                                     if _match_pred(by_id[ev.id], pred)})]
                             for r in model.rules]
    if k == "absence" and p.get("scope", "views") == "views":
        pred = p.get("match") or {}
        roles = [p["role"]] if p.get("role") is not None else list(views)
        return "projected", [[role, None if role not in views else
                              (bool(views[role]), sorted(ev.id for ev, _ in views[role] if _match_pred(ev, pred)))]
                             for role in roles]
    if k == "epistemic" and p.get("rule") == "label_isolation":
        view = views.get(p["role"])
        return "projected", None if view is None else sorted(ev.id for ev, _ in view if p["label"] in ev.policy_labels)
    if k == "epistemic" and p.get("rule", "no_future_knowledge") == "no_future_knowledge":
        # decisions cite exactly what was received by at_ns, so only early receipts can fail
        return "projected", sorted((role, ev.id) for role, v in views.items() for ev, rt in v if rt < ev.event_time_ns)
    if k == "epistemic" and p.get("rule") == "permitted_only":
        vis = {r.id: list(r.visibility) for r in model.scenario.roles}

        def bad(r, known, idx):
            v = vis.get(r.role)
            if v is None or "*" in v:
                return [] if v is not None else None
            return sorted({ev.id for ev, _ in known if not all(lbl in v for lbl in idx[ev.id][0].policy_labels)})
        return "projected", _decision_known_component(model, views, p, bad)
    return "exact", _exact_views(views)


def projection(model: DeliveryModel, invariants: dict[str, Invariant], views: dict[str, list[Delivery]]) -> list:
    """The signature's pre-image: decision components, per-invariant components, view kinds."""
    rules_part = []
    for r in model.rules:
        rel = [(ev.id, ev.content_hash, rt >= r.at_ns - r.window_ns, rt if r.time_sensitive else None)
               for ev, rt in _known(views, r) if r.kinds is None or ev.kind in r.kinds]
        rules_part.append([r.decision_id, rel])
    inv_part = [[inv.id, *invariant_component(model, inv, views)]
                for inv in sorted(invariants.values(), key=lambda i: i.id)]
    kinds = sorted({ev.kind for v in views.values() for ev, _ in v})  # required_signals
    return [rules_part, inv_part, kinds]


def projection_modes(model: DeliveryModel, invariants: dict[str, Invariant]) -> dict[str, str]:
    """invariant id -> "projected" | "exact" (exact = no reduction beyond identical views)."""
    views = model.predict(())
    return {inv.id: invariant_component(model, inv, views)[0] for inv in invariants.values()}


def schedule_signature(model: DeliveryModel, invariants: dict[str, Invariant],
                       schedule: Sequence[Intervention]) -> str | None:
    """Canonical equivalence signature of a schedule, or None when it must be run to be judged
    (the runner would reject it)."""
    views = model.predict(schedule)
    if views is None:
        return None
    return content_hash(projection(model, invariants, views))


# ------------------------------------------------------------------ delay flip thresholds

def _rule(model: DeliveryModel, rule_id: str) -> RuleInfo:
    for r in model.rules:
        if r.rule_id == rule_id:
            return r
    raise KeyError(f"unknown decision rule {rule_id!r}")


def delay_breakpoints(model: DeliveryModel, event_id: str, rule: RuleInfo) -> list[int]:
    """Delays (ns, > 0) at which delaying ``event_id`` to the rule's role can change what the rule sees.

    Between consecutive breakpoints the delayed delivery keeps its position relative to the decision
    time, the window start, the horizon and every other delivery to that role, so the rule's input
    (known set, windowed set, receipt order) and hence its option are constant.
    """
    rt = model.root_receipt(rule.role, event_id)
    if rt is None:
        return []
    pts = {rule.at_ns - rt + 1, rule.at_ns - rule.window_ns - rt, model.scenario.horizon_ns - rt + 1}
    for v in rule_receipt_time_literals(rule.when):
        pts |= {v - rt, v - rt + 1}
    for role, ev, rt_o in model.base:
        if role == rule.role and ev.id != event_id and rt_o >= rt:
            pts |= {rt_o - rt, rt_o - rt + 1}
    return sorted(p for p in pts if p > 0)


def flip_threshold(scenario: Scenario, event_id: str, role: str | None, rule_id: str, *,
                   root: Trace | None = None, model: DeliveryModel | None = None) -> int | None:
    """Smallest ``by_ns`` for which ``delay(event_id, role, by_ns)`` changes ``rule_id``'s option.

    Solved from the root's receipt times: the option is piecewise constant in the delay with changes
    only at ``delay_breakpoints``, so evaluating the rule once per breakpoint (in increasing order) on
    the predicted view gives the exact threshold. None when no delay flips the rule (or ``role`` is not
    the rule's actor role). ``role=None`` delays the event to every role.
    """
    model = model or build_model(scenario, root)
    rule = _rule(model, rule_id)
    if role is not None and role != rule.role:
        return None
    base_opt = model.decide(model.predict(()))[rule.decision_id]
    target = {"event_id": event_id} if role is None else {"event_id": event_id, "role": role}
    for by in delay_breakpoints(model, event_id, rule):
        opts = model.predict_decisions((Intervention("delay", target, {"by_ns": by}),))
        if opts is not None and opts[rule.decision_id] != base_opt:
            return by
    return None


# ------------------------------------------------------------------ decision-window candidates

def window_candidates(model: DeliveryModel, rng: DeterministicRNG) -> list[tuple[Intervention, bool]]:
    """Interventions that move a decision-relevant delivery across a decision boundary, or drop or
    corrupt one the decision knows. Each is paired with whether it is predicted to flip a decision.

    Boundaries per (rule, relevant delivery to the rule's role, root receipt rt):
      exit    delay by at_ns - rt + 1             (known -> unknown at the decision)
      enter   delay by at_ns - window_ns - rt     (before the window -> inside it)
      flip    delay by flip_threshold(...)        (smallest delay that changes the option)
      drop    drop for that role                  (if known at the decision)
      corrupt each payload field the predicate reads, for that role (if known at the decision)
    """
    root_opts = model.decide(model.predict(()))
    out: list[Intervention] = []
    seen: set[str] = set()

    def add(iv: Intervention) -> None:
        h = iv.hash()
        if h not in seen:
            seen.add(h)
            out.append(iv)

    for r in model.rules:
        fields = sorted(rule_payload_fields(r.when))
        for role, ev, rt in model.base:
            if role != r.role or (r.kinds is not None and ev.kind not in r.kinds):
                continue
            if rt > model.scenario.horizon_ns:
                continue  # never delivered in the root world
            tgt = {"event_id": ev.id, "role": role}
            if rt <= r.at_ns:
                add(Intervention("delay", tgt, {"by_ns": r.at_ns - rt + 1}))
            if rt < r.at_ns - r.window_ns:
                add(Intervention("delay", tgt, {"by_ns": r.at_ns - r.window_ns - rt}))
            thr = flip_threshold(model.scenario, ev.id, role, r.rule_id, model=model)
            if thr is not None:
                add(Intervention("delay", tgt, {"by_ns": thr}))
            if rt <= r.at_ns:
                add(Intervention("drop", tgt, {}))
                for k in fields:
                    v = (ev.payload or {}).get(k)
                    if isinstance(v, (int, float, str, bool)):
                        val = _corrupt_value(v, rng.fork(f"corrupt/{r.decision_id}/{ev.id}/{k}"))
                        add(Intervention("corrupt", tgt, {"field": f"payload.{k}", "value": val}))
    flagged = []
    for iv in out:
        opts = model.predict_decisions((iv,))
        flagged.append((iv, opts is not None and opts != root_opts))
    return flagged
