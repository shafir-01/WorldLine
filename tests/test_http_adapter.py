"""HTTP adapter: the checker, minimizer and genome run against a real FastAPI service."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from worldline.core import Verdict  # noqa: E402
from worldline.adapters.http import (  # noqa: E402
    HttpWorld, challenge_http, demo_service, explore_http, minimize_http, verdicts_http,
)
from worldline.genome import FailureGenome  # noqa: E402
from worldline.interventions import Intervention  # noqa: E402
from worldline.invariants import load_invariants  # noqa: E402
from worldline.worlds import load_scenario, run  # noqa: E402

S = 1_000_000_000
ROOT = Path(__file__).resolve().parents[1]
TARGET = "decision.consistent_with_truth"


@pytest.fixture(scope="module")
def sc():
    return load_scenario(ROOT / "scenarios" / "port_alert.yaml")


@pytest.fixture(scope="module")
def inv(sc):
    allinv = load_invariants(ROOT / "invariants")
    return {k: v for k, v in allinv.items() if k in sc.invariants}


@pytest.fixture(scope="module")
def world(sc):
    return HttpWorld(demo_service(), sc)


def delay_correction(by_s: int) -> Intervention:
    return Intervention("delay", {"event_id": "correction-001", "role": "service"}, {"by_ns": by_s * S})


def verdict(world, tr, inv, inv_id):
    return next(v for v in verdicts_http(world, tr, inv) if v.invariant_id == inv_id)


def test_root_decision_matches_simulator(sc, world, inv):
    sim = run(sc).decisions
    sim_opt = next(d.payload["option"] for d in sim if d.payload["rule_id"] == "escalate_or_monitor")
    tr = world.run()
    assert [d.payload["option"] for d in tr.decisions] == [sim_opt] == ["monitor"]
    assert not tr.rejected()
    assert verdict(world, tr, inv, TARGET).result == Verdict.PASS
    # the decision cites exactly what the service says it had received by 1500s
    d = tr.decisions[0]
    known = [e.id for e in tr.views["service"] if e.received_time_ns <= 1500 * S]
    assert sorted(d.payload["known_event_ids"]) == sorted(known)
    assert "correction-001" in known


def test_delayed_correction_flips_service_decision_and_fails(world, inv):
    # baseline receipt ~1409s; +120s lands it after the 1500s decision
    tr = world.run((delay_correction(120),))
    rt = next(e.received_time_ns for e in tr.views["service"] if e.id == "correction-001")
    assert rt > 1500 * S
    assert tr.decisions[0].payload["option"] == "escalate"
    v = verdict(world, tr, inv, TARGET)
    assert v.result == Verdict.FAIL
    assert v.witness[0] == "correction-001"


def test_deterministic_digests(sc, world):
    sched = (delay_correction(120), Intervention("duplicate", {"event_id": "sighting-002"}, {"count": 2}))
    a, b = world.run(sched), world.run(sched)
    assert a.digest == b.digest
    # a fresh service instance gives the same digest too
    c = HttpWorld(demo_service(), sc).run(sched)
    assert c.digest == a.digest
    assert world.run().digest == world.run().digest
    assert a.exchanges_digest == b.exchanges_digest == c.exchanges_digest


def test_service_validation_rejection_is_absent_from_view(world):
    tr = world.run((Intervention("corrupt", {"event_id": "sighting-002"},
                                 {"field": "payload.confidence", "value": -0.8}),))
    assert [x["event_id"] for x in tr.rejected()] == ["sighting-002"]
    assert "sighting-002" not in {e.id for e in tr.views["service"]}


def test_service_agrees_with_dsl_rule_on_perturbed_worlds(world):
    """Differential check: the service's decision equals the scenario DSL's over the same deliveries."""
    scheds = [(), (delay_correction(120),), (delay_correction(60),),
              (Intervention("drop", {"event_id": "sighting-002"}, {}),),
              (Intervention("duplicate", {"event_id": "sighting-002"}, {"count": 1}),),
              (Intervention("corrupt", {"event_id": "correction-001"}, {"field": "payload.corrects", "value": ""}),)]
    for s in scheds:
        assert ([d.payload["option"] for d in world.run(s).decisions]
                == [d.payload["option"] for d in run(world.scenario, s).decisions]), s


def test_ddmin_reaches_one_intervention(world, inv):
    noisy = (
        Intervention("clock_skew", {}, {"source_id": "tide_gauge", "offset_ns": 5 * S}),
        Intervention("delay", {"event_id": "ais-003"}, {"by_ns": 30 * S}),
        delay_correction(300),
        Intervention("duplicate", {"event_id": "weather-001"}, {"count": 2}),
    )
    res = minimize_http(world, None, inv, noisy, TARGET)
    assert len(res.minimal) == 1
    assert res.minimal[0]["target"]["event_id"] == "correction-001"
    assert res.one_minimal_verified


def test_explore_http_finds_decision_failure(world, inv):
    res = explore_http(world, None, inv, budget=15, seed=42, strategy="guided", schedule_size=2)
    assert res.nodes[0].branch_id == "root"
    assert any(TARGET in n.failing_invariants for n in res.nodes)
    again = explore_http(world, None, inv, budget=15, seed=42, strategy="guided", schedule_size=2)
    assert [n.trace_digest for n in res.nodes] == [n.trace_digest for n in again.nodes]


def test_simulator_genome_reproduces_against_service(world, inv):
    gpath = ROOT / "experiments" / "out" / "genome.json"
    if not gpath.exists():
        pytest.skip("run `worldline demo` first to extract the port_alert genome")
    g = FailureGenome.from_dict(json.loads(gpath.read_text(encoding="utf-8")))
    res = challenge_http(g, None, world, inv)
    assert res.preconditions_met
    assert res.instantiated_schedule[0]["target"]["event_id"] == "correction-001"
    assert res.reproduced
