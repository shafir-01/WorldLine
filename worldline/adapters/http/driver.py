"""HttpWorld: drive a real ASGI service with truth events, delivered under a FaultSchedule.

The simulator answers "what would a rule decide if it had received these events at these times".
HttpWorld asks the SERVICE instead. For each world:

1. Truth is the scenario's injects as Event v1 (the same function the simulator runner uses).
2. Receipt times come from the simulator's own delivery path: the role's DeliveryPolicy with the
   per-(role, event) RNG fork, outage transitions, FIFO clamping, horizon, and the delivery hook
   built by ``worldline.interventions.hook_for_schedule``. Nothing is re-implemented here.
3. The driver sends every surviving delivery to the service over httpx's ASGI transport, in
   received order, with ``X-Worldline-Now`` = receipt time (virtual time; no wall clock).
4. At each decision time it calls ``GET /decision?at=t`` and records the answer as a decision
   Event whose ``known_event_ids`` are the ids the SERVICE says it had received.
5. The view is rebuilt from the service's receive log (``GET /log``), not from the send plan.

The result is a real ``worldline.worlds.Trace`` (subclass ``HttpTrace``) so the checker, the
mutators, ``view_as_known_at``, ``causal_diff`` and the genome code run on it unchanged.

Projection. The service implements one decision rule for one deciding party. HttpWorld therefore
runs a projection of the scenario: one role (``service``, default) with the deciding actor's
delivery policy (or one supplied), and the one actor whose rule the service implements, rebound
to that role. Truth, seed, clock and transitions are the scenario's own.
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Sequence

import httpx

from worldline.core import DeterministicRNG, Event, EventLog, VirtualClock, content_hash
from worldline.interventions import hook_for_schedule, schedule_hash
from worldline.worlds import Actor, DeliveryPolicy, Role, Scenario, Trace, TruthTransition, identity_hook
from worldline.worlds.runner import _deliver_role, _truth_events  # the simulator's own delivery path

from .recorder import DEFAULT_ROUTES, to_request, view_from_log

NOW_HEADER = "X-Worldline-Now"
CLIENT_HEADER = "X-Worldline-Client"
REPO_ROOT = Path(__file__).resolve().parents[3]
DEMO_APP_PATH = REPO_ROOT / "examples" / "service_demo" / "app.py"
RuleEvaluator = Callable[[str, list[Event]], str]


# ------------------------------------------------------------------ the service under test

@dataclass
class ServiceUnderTest:
    """A running ASGI app plus a direct handle on its decision rule.

    ``evaluate_rule(rule_id, events) -> option`` calls the service's OWN rule function over an
    arbitrary event set (the checker uses it over truth-as-of); it does not go through HTTP.
    """
    name: str
    app: Any
    rule_ids: tuple[str, ...]
    evaluate_rule_at: Callable[[list[Event], int], str]
    routes: dict = field(default_factory=lambda: dict(DEFAULT_ROUTES))


def _load_module(path: Path, name: str) -> ModuleType:
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise ImportError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def demo_service(path: Path | str = DEMO_APP_PATH) -> ServiceUnderTest:
    """The example approvals service (examples/service_demo/app.py), freshly instantiated."""
    mod = _load_module(Path(path), "worldline_demo_service_app")
    routes = dict(DEFAULT_ROUTES)

    def evaluate_at(events: list[Event], at_ns: int) -> str:
        records = []
        for e in events:
            _, body = to_request(e, routes)
            channel = routes.get(e.kind, ("", "observation"))[1]
            records.append(mod.Record(
                id=e.id, channel=channel,
                received_at_ns=e.received_time_ns if e.received_time_ns is not None else e.event_time_ns,
                observed_at_ns=e.event_time_ns, source_id=e.source_id,
                confidence=body.get("confidence") if channel in ("report", "correction") else None,
                corrects=body.get("corrects") if channel == "correction" else None, body=body))
        return mod.escalation_decision(records, at_ns)["option"]

    return ServiceUnderTest("approvals-demo", mod.create_app(), (mod.RULE_ID,), evaluate_at, routes)


# ------------------------------------------------------------------ trace

@dataclass
class HttpTrace(Trace):
    """A Trace whose views and decisions came from a real service. ``exchanges`` is the HTTP
    conversation (path, virtual now, status). ``digest`` is the plain Trace digest (so the JSON
    loads back with ``Trace.from_dict``); the conversation has its own ``exchanges_digest``."""
    exchanges: list[dict] = field(default_factory=list)
    service: str = ""

    @property
    def exchanges_digest(self) -> str:
        return content_hash({"service": self.service, "exchanges": self.exchanges})

    def rejected(self) -> list[dict]:
        return [x for x in self.exchanges if x["status"] >= 400]

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["adapter"] = "http"
        d["http"] = {"service": self.service, "exchanges": self.exchanges, "rejected": self.rejected(),
                     "exchanges_digest": self.exchanges_digest}
        return d


# ------------------------------------------------------------------ the world

def project_scenario(scenario: Scenario, rule_ids: Sequence[str], role: str = "service",
                     policy: DeliveryPolicy | None = None) -> Scenario:
    """One role, one actor: the actor whose rule(s) the service implements, rebound to ``role``."""
    actor = next((a for a in scenario.actors if any(r.id in rule_ids for r in a.decision_rules)), None)
    if actor is None:
        raise ValueError(f"scenario {scenario.id!r} has no actor with rule(s) {list(rule_ids)}")
    pol = policy or scenario.role(actor.role).delivery
    rules = [r for r in actor.decision_rules if r.id in rule_ids]
    transitions = []
    for t in scenario.transitions:
        if t.roles and actor.role not in t.roles:
            continue
        transitions.append(TruthTransition(t.id, t.kind, t.from_ns, t.to_ns, t.source_id, [], dict(t.params)))
    sc = Scenario(id=scenario.id, seed=scenario.seed, clock=dict(scenario.clock),
                  roles=[Role(id=role, delivery=pol, visibility=["*"])],
                  actors=[Actor(id=actor.id, role=role, decision_rules=rules)],
                  injects=list(scenario.injects), transitions=transitions, invariants=list(scenario.invariants))
    sc.validate()
    return sc


class HttpWorld:
    def __init__(self, service: ServiceUnderTest, scenario: Scenario, *, policy: DeliveryPolicy | None = None,
                 role: str = "service", client_id: str = "worldline"):
        self.service = service
        self.source_scenario = scenario
        self.role = role
        self.client_id = client_id
        self.scenario = project_scenario(scenario, service.rule_ids, role, policy)
        self.truth_events = _truth_events(self.scenario)
        self._rules = {r.id: r for a in self.scenario.actors for r in a.decision_rules}

    # the checker's decision.consistent_with_truth evaluator: the service's rule, called directly
    def evaluate_rule(self, rule_id: str, events: list[Event]) -> str:
        return self.service.evaluate_rule_at(list(events), self._rules[rule_id].at_ns)

    def plan(self, schedule: Sequence[Any] = ()) -> EventLog:
        """Receipt times for every delivery, via the simulator's delivery path + hook_for_schedule.
        Raises ValueError when a schedule breaks the time rule (as ``worldline.worlds.run`` does)."""
        schedule = tuple(schedule)
        hook = hook_for_schedule(schedule, self.truth_events) if schedule else identity_hook
        return _deliver_role(self.scenario, self.role, self.truth_events, DeterministicRNG(self.scenario.seed),
                             hook, None, None)

    def run(self, schedule: Sequence[Any] = ()) -> HttpTrace:
        schedule = tuple(schedule)
        planned = self.plan(schedule)
        decisions, exchanges, log_json = asyncio.run(self._drive(planned))
        truth = EventLog()
        for e in self.truth_events:
            truth.append(e)
        clock = VirtualClock(epoch_ns=self.scenario.clock["epoch_ns"], tick_ns=self.scenario.clock["tick_ns"])
        clock.advance_to(max(clock.now_ns, self.scenario.horizon_ns))
        fs_hash = schedule_hash(schedule)
        tr = HttpTrace(
            scenario_id=self.scenario.id, seed=self.scenario.seed,
            branch_id="root" if not schedule else f"root/{fs_hash}", fault_schedule_hash=fs_hash,
            truth=truth, views={self.role: view_from_log(log_json, self.scenario.id, self.service.routes)},
            decisions=decisions, clock_end=clock.snapshot(), scenario=self.scenario,
            exchanges=exchanges, service=self.service.name)
        tr.digest = tr.compute_digest()
        return tr

    async def _drive(self, planned: EventLog) -> tuple[list[Event], list[dict], dict]:
        due = sorted(((r.at_ns, a.id, r.id) for a in self.scenario.actors for r in a.decision_rules))
        decisions: list[Event] = []
        exchanges: list[dict] = []
        transport = httpx.ASGITransport(app=self.service.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://service",
                                     headers={CLIENT_HEADER: self.client_id}) as client:
            r = await client.post("/reset")
            r.raise_for_status()

            async def decide(at_ns: int, actor_id: str, rule_id: str) -> None:
                resp = await client.get("/decision", params={"at": at_ns}, headers={NOW_HEADER: str(at_ns)})
                exchanges.append({"seq": len(exchanges), "method": "GET", "path": "/decision", "event_id": None,
                                  "now_ns": at_ns, "status": resp.status_code})
                resp.raise_for_status()
                body = resp.json()
                decisions.append(Event(
                    id=f"decision:{actor_id}:{rule_id}", scenario_id=self.scenario.id, source_id=actor_id,
                    event_time_ns=at_ns, kind="decision", received_time_ns=at_ns,
                    payload={"actor": actor_id, "rule_id": rule_id, "role": self.role, "option": body["option"],
                             "known_event_ids": list(body["known_event_ids"]), "at_ns": at_ns,
                             "predicate_value": bool(body["predicate_value"]),
                             "credible_report_ids": list(body.get("credible_report_ids", []))}))

            for ev in planned:  # already in received order (received_time, canonical key)
                rt = int(ev.received_time_ns)
                while due and due[0][0] < rt:  # deliveries at exactly t are known at t
                    await decide(*due.pop(0))
                path, body = to_request(ev, self.service.routes)
                resp = await client.post(path, json=body, headers={NOW_HEADER: str(rt)})
                exchanges.append({"seq": len(exchanges), "method": "POST", "path": path, "event_id": ev.id,
                                  "now_ns": rt, "status": resp.status_code})
            while due:
                await decide(*due.pop(0))
            log_json = (await client.get("/log")).json()
        decisions.sort(key=lambda d: (d.event_time_ns, d.payload["actor"], d.payload["rule_id"]))
        return decisions, exchanges, log_json
