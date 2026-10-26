"""FailureGenome: a failure mechanism abstracted far enough to challenge another scenario.

The minimal fault schedule found by the explorer names concrete event ids
("delay correction-001 to harbour_ops by 120 s"). That is useless anywhere else.
The genome keeps only the *structure* of the mechanism:

    delay the last decision-relevant event of kind K that role R received before
    decision rule D, until after D's decision time.

Preconditions are structural facts about a scenario that the mechanism needs.
Challenging a second scenario means: check preconditions, instantiate the
structural selectors against that scenario's baseline trace, run, and check
whether the same *class* of invariant fails. Both outcomes are recorded.

Claim ceiling: transfer is between scenarios that share the Event v1 substrate.
It is not transfer across heterogeneous codebases.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from worldline.core import EvidenceKind, Verdict, content_hash
from worldline.interventions import Intervention
from worldline.invariants import Invariant, VerdictRecord, check_all
from worldline.worlds import Scenario, Trace, run, view_as_known_at
from worldline.worlds.predicate import parse_predicate
from worldline.explorer.explore import rule_evaluator, decision_dependencies


# ------------------------------------------------------------------ data

@dataclass
class StructuralSelector:
    """Where the intervention lands, described relative to a decision, not by id."""
    kind: str                       # event kind targeted (e.g. "correction")
    relation: str                   # "corrects_dependency" | "dependency" | "any_of_kind"
    position: str                   # "last_before_decision" | "first_before_decision"
    role: str = "decision_role"     # symbolic: the deciding actor's role
    def to_dict(self) -> dict: return asdict(self)


@dataclass
class MechanismStep:
    op: str                         # delay | drop | corrupt | duplicate
    selector: StructuralSelector
    params: dict                    # delay: {"until": "after_decision", "margin_ns": int}; corrupt: {"field", "value"}
    def to_dict(self) -> dict:
        return {"op": self.op, "selector": self.selector.to_dict(), "params": self.params}


@dataclass
class FailureGenome:
    violated_invariant: str          # invariant id (version in origin)
    mechanism: list[MechanismStep]
    preconditions: list[str]
    witness_shape: dict
    origin: dict
    evidence_kind: EvidenceKind = EvidenceKind.EMPIRICALLY_VALIDATED
    claim_ceiling: str = ("transfer within the shared Event v1 substrate across scenarios; "
                          "not across heterogeneous codebases")
    id: str = ""

    def __post_init__(self):
        if not self.id:
            self.id = "genome:" + content_hash({
                "violated_invariant": self.violated_invariant,
                "mechanism": [m.to_dict() for m in self.mechanism],
                "preconditions": self.preconditions})[7:23]

    def to_dict(self) -> dict:
        return {"id": self.id, "violated_invariant": self.violated_invariant,
                "mechanism": [m.to_dict() for m in self.mechanism], "preconditions": self.preconditions,
                "witness_shape": self.witness_shape, "origin": self.origin,
                "evidence_kind": self.evidence_kind.value, "claim_ceiling": self.claim_ceiling}

    @staticmethod
    def from_dict(d: dict) -> "FailureGenome":
        mech = [MechanismStep(m["op"], StructuralSelector(**m["selector"]), m["params"]) for m in d["mechanism"]]
        return FailureGenome(d["violated_invariant"], mech, d["preconditions"], d["witness_shape"], d["origin"],
                             EvidenceKind(d.get("evidence_kind", "EMPIRICALLY_VALIDATED")),
                             d.get("claim_ceiling", ""), d.get("id", ""))


@dataclass
class ChallengeResult:
    genome_id: str
    scenario_id: str
    preconditions_met: bool
    preconditions_report: dict[str, bool]
    instantiated_schedule: list[dict]
    target_rule: str | None
    verdicts: list[dict]
    reproduced: bool
    counterexample_digest: str | None
    explanation: str
    def to_dict(self) -> dict: return asdict(self)


# ------------------------------------------------------------------ extraction

def _rule_for_decision_witness(scenario: Scenario, trace: Trace, verdict: VerdictRecord) -> tuple[str, dict] | None:
    """Find which decision rule the FAIL is about: the one whose option differs from truth."""
    ev = rule_evaluator(scenario)
    truth_events = [e.with_receipt(e.event_time_ns) for e in trace.truth]
    deps = decision_dependencies(scenario)
    for d in trace.decisions:
        rid = d.payload["rule_id"]
        if rid not in deps:
            continue
        truth_opt = ev(rid, [e for e in truth_events if e.event_time_ns <= d.payload["at_ns"]])
        if truth_opt != d.payload["option"]:
            return rid, deps[rid]
    return None


def extract(trace: Trace, minimal_schedule: list[Intervention], verdict: VerdictRecord, scenario: Scenario,
            *, origin_extra: dict | None = None) -> FailureGenome:
    truth = {e.id: e for e in trace.truth}
    deps = decision_dependencies(scenario)
    found = _rule_for_decision_witness(scenario, trace, verdict)
    rule_id, dep = found if found else (None, None)
    steps: list[MechanismStep] = []
    pre: list[str] = []
    for iv in minimal_schedule:
        eid = (iv.target.get("event_id") or "").split("#")[0]
        ev = truth.get(eid)
        if ev is None:
            # untargeted ops (revoke/clock_skew) are kept with their role made symbolic
            steps.append(MechanismStep(iv.op, StructuralSelector("*", "any_of_kind", "first_before_decision"),
                                       {k: v for k, v in iv.params.items() if k not in ("role", "source_id")}))
            continue
        corrects = ev.payload.get("corrects")
        if corrects and corrects in truth and dep and truth[corrects].kind in dep["kinds"]:
            relation = "corrects_dependency"
            pre.append("decision_rule_depends_on_a_corrected_kind")
            pre.append(f"exists_kind:{ev.kind}:with_payload_field:corrects")
        elif dep and ev.kind in dep["kinds"]:
            relation = "dependency"
            pre.append(f"decision_rule_depends_on_kind:{ev.kind}")
        else:
            relation = "any_of_kind"
            pre.append(f"exists_kind:{ev.kind}")
        params = dict(iv.params)
        params.pop("role", None)
        if iv.op == "delay" and dep:
            params = {"until": "after_decision", "margin_ns": 1_000_000_000}
            pre.append("event_received_by_decision_role_before_decision")
        steps.append(MechanismStep(iv.op, StructuralSelector(ev.kind, relation, "last_before_decision"), params))
    pre.append("has_decision_rule")
    pre = sorted(set(pre))
    witness_shape = {"kind": "truth_events_missing_from_decision_role_view_at_decision_time",
                     "n_witness_events": len(verdict.witness),
                     "decision_rule": rule_id, "decision_actor": dep["actor"] if dep else None}
    origin = {"scenario_id": scenario.id, "seed": trace.seed, "branch_id": trace.branch_id,
              "trace_digest": trace.digest, "fault_schedule_hash": trace.fault_schedule_hash,
              "invariant_version": verdict.invariant_version, "witness": verdict.witness, **(origin_extra or {})}
    return FailureGenome(verdict.invariant_id, steps, pre, witness_shape, origin)


# ------------------------------------------------------------------ challenge

def _check_preconditions(genome: FailureGenome, scenario: Scenario, root: Trace) -> dict[str, bool]:
    deps = decision_dependencies(scenario)
    kinds_truth = {e.kind for e in root.truth}
    dep_kinds = {k for d in deps.values() for k in d["kinds"]}
    report = {}
    for p in genome.preconditions:
        if p == "has_decision_rule":
            report[p] = bool(deps)
        elif p.startswith("decision_rule_depends_on_kind:"):
            report[p] = p.split(":", 1)[1] in dep_kinds
        elif p == "decision_rule_depends_on_a_corrected_kind":
            truth = {e.id: e for e in root.truth}
            report[p] = any(
                (c := e.payload.get("corrects")) and c in truth and truth[c].kind in dep_kinds
                for e in root.truth)
        elif p.startswith("exists_kind:"):
            parts = p.split(":")
            kind = parts[1]
            ok = kind in kinds_truth
            if len(parts) >= 4 and parts[2] == "with_payload_field":
                ok = ok and any(e.kind == kind and parts[3] in e.payload for e in root.truth)
            report[p] = ok
        elif p == "event_received_by_decision_role_before_decision":
            report[p] = any(view_as_known_at(root, d["role"], d["at_ns"]) for d in deps.values())
        else:
            report[p] = False
    return report


def _instantiate(genome: FailureGenome, scenario: Scenario, root: Trace) -> tuple[list[Intervention], str | None, str]:
    """Pick the decision rule and concrete events that the structural selectors describe."""
    deps = decision_dependencies(scenario)
    truth = {e.id: e for e in root.truth}
    best: tuple[list[Intervention], str | None, str] = ([], None, "no rule matched the mechanism's structure")
    for rid, d in sorted(deps.items()):
        known = view_as_known_at(root, d["role"], d["at_ns"])
        schedule: list[Intervention] = []
        notes = []
        ok = True
        for step in genome.mechanism:
            sel = step.selector
            cands = []
            for e in known:
                if sel.kind != "*" and e.kind != sel.kind:
                    continue
                if sel.relation == "corrects_dependency":
                    c = e.payload.get("corrects")
                    if not (c and c in truth and truth[c].kind in d["kinds"]):
                        continue
                elif sel.relation == "dependency" and e.kind not in d["kinds"]:
                    continue
                cands.append(e)
            if not cands:
                ok = False
                notes.append(f"{rid}: no event for selector {sel.to_dict()}")
                break
            cands.sort(key=lambda e: e.received_time_ns)
            e = cands[-1] if sel.position == "last_before_decision" else cands[0]
            params = dict(step.params)
            if step.op == "delay" and params.get("until") == "after_decision":
                by = d["at_ns"] - e.received_time_ns + int(params.get("margin_ns", 1_000_000_000))
                params = {"by_ns": max(1, by), "role": d["role"]}
            else:
                params["role"] = d["role"]
            schedule.append(Intervention(step.op, {"event_id": e.id, "role": d["role"]}, params))
            notes.append(f"{rid}: {step.op} {e.id} ({e.kind}) for role {d['role']}")
        if ok and schedule:
            return schedule, rid, "; ".join(notes)
        best = (best[0], best[1], "; ".join(notes) or best[2])
    return best


def challenge(genome: FailureGenome, scenario: Scenario, invariants: dict[str, Invariant], *, now_ns: int = 0) -> ChallengeResult:
    root = run(scenario)
    pre = _check_preconditions(genome, scenario, root)
    met = all(pre.values())
    schedule, rid, note = _instantiate(genome, scenario, root)
    if not met or not schedule:
        return ChallengeResult(genome.id, scenario.id, met, pre, [i.to_dict() for i in schedule], rid, [], False,
                               None, f"not instantiated: preconditions_met={met}; {note}")
    tr = run(scenario, tuple(schedule))
    ev = rule_evaluator(scenario)
    target = {genome.violated_invariant: invariants[genome.violated_invariant]} if genome.violated_invariant in invariants else invariants
    vs = check_all(tr, target, scenario=scenario, evaluate_rule=ev, now_ns=now_ns)
    root_vs = check_all(root, target, scenario=scenario, evaluate_rule=ev, now_ns=now_ns)
    root_fail = {v.invariant_id for v in root_vs if v.result == Verdict.FAIL}
    new_fail = [v for v in vs if v.result == Verdict.FAIL and v.invariant_id not in root_fail]
    reproduced = bool(new_fail)
    expl = (f"reproduced on {scenario.id}: {new_fail[0].explanation}" if reproduced
            else f"preconditions met, mechanism instantiated ({note}), but no new {genome.violated_invariant} FAIL")
    return ChallengeResult(genome.id, scenario.id, met, pre, [i.to_dict() for i in schedule], rid,
                           [v.to_dict() for v in vs], reproduced, tr.digest if reproduced else None, expl)
