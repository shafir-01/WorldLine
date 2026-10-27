"""USGS real-data adapter, fully offline: uses the committed snapshot, never the network."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from worldline.adapters import usgs
from worldline.core import Verdict
from worldline.explorer.explore import rule_evaluator
from worldline.interventions import Intervention
from worldline.invariants import check_all, load_invariants
from worldline.worlds import load_scenario, run

ROOT = Path(__file__).resolve().parents[1]
KEY = "20250729T2320Z_20250730T0140Z_m5.5"
SNAP_REF = f"data/real/usgs/{KEY}.json"
SNAP = ROOT / SNAP_REF
YAML = ROOT / "scenarios" / "real" / f"usgs_{KEY}.yaml"
TARGET = "decision.consistent_with_truth"
CORR = "corr-us6000qw60-1"          # real: USGS upgrade M8.0 -> M8.7, published 2025-07-30T00:22:51.040Z
S = 1_000_000_000


@pytest.fixture(scope="module")
def sc():
    return load_scenario(YAML)


@pytest.fixture(scope="module")
def inv():
    return load_invariants(ROOT / "invariants")


def _check(tr, sc, inv):
    return {v.invariant_id: v for v in check_all(tr, {k: inv[k] for k in sc.invariants}, scenario=sc,
                                                 evaluate_rule=rule_evaluator(sc), now_ns=0)}


def test_provenance_hash_matches_and_detects_tampering():
    doc = usgs.load_snapshot(SNAP)
    prov = doc["provenance"]
    assert prov["license"] == "USGS, public domain"
    assert prov["source_url"].startswith("https://earthquake.usgs.gov/fdsnws/event/1/query?")
    assert prov["fetched_at_utc"] and len(prov["sha256"]) == 64
    assert usgs.verify_snapshot(doc)
    bad = copy.deepcopy(doc)
    bad["payload"]["summary"]["features"][0]["properties"]["mag"] = 9.9
    assert not usgs.verify_snapshot(bad)


def test_snapshot_is_small():
    assert SNAP.stat().st_size < 400_000


def test_offline_regeneration_is_byte_identical(tmp_path):
    out = tmp_path / "regen.yaml"
    usgs.to_scenario(SNAP, out, snapshot_ref=SNAP_REF)
    assert out.read_bytes() == YAML.read_bytes()


def test_scenario_records_provenance_and_model_labels():
    raw = YAML.read_text(encoding="utf-8")
    assert "# REAL" in raw and "# MODEL" in raw
    import yaml
    d = yaml.safe_load(raw)
    assert d["provenance"]["snapshot_sha256"] == usgs.load_snapshot(SNAP)["provenance"]["sha256"]
    assert d["provenance"]["model"] and d["provenance"]["real"]


def test_loads_and_runs_deterministically(sc):
    a, b = run(sc), run(sc)
    assert a.digest == b.digest
    assert load_scenario(YAML).to_dict() == sc.to_dict()


def test_real_corrections_exist_and_point_at_earlier_estimates(sc):
    by_id = {i.id: i for i in sc.injects}
    corrections = [i for i in sc.injects if i.kind == "correction"]
    assert corrections
    for c in corrections:
        target = by_id[c.payload["corrects"]]
        assert target.kind in ("quake_report", "correction")
        assert target.at_ns < c.at_ns
        assert by_id[c.payload["report"]].kind == "quake_report"
    c = by_id[CORR]
    assert (c.payload["previous_mag"], c.payload["mag"]) == (8.0, 8.7)
    assert c.payload["published_utc"] == "2025-07-30T00:22:51.040Z"


def test_baseline_decision_recorded_and_consistent(sc, inv):
    tr = run(sc)
    (d,) = tr.decisions
    assert d.payload["actor"] == "duty_seismologist"
    assert d.payload["option"] == "issue_advisory"
    assert CORR in d.payload["known_event_ids"]
    vs = _check(tr, sc, inv)
    assert all(v.result == Verdict.PASS for v in vs.values()), {k: v.explanation for k, v in vs.items()}


def _delayed(sc, by_s: int):
    iv = Intervention("delay", {"event_id": CORR, "role": "regional_hq"}, {"by_ns": by_s * S, "role": "regional_hq"})
    return run(sc, (iv,))


def test_delaying_the_real_correction_past_the_decision_flips_it_and_fails(sc, inv):
    root = run(sc)
    rt = next(e.received_time_ns for e in root.views["regional_hq"] if e.id == CORR)
    at = sc.actors[0].decision_rules[0].at_ns
    assert rt < at
    tr = _delayed(sc, (at - rt) // S + 1)
    (d,) = tr.decisions
    assert d.payload["option"] == "hold"
    v = _check(tr, sc, inv)[TARGET]
    assert v.result == Verdict.FAIL
    assert CORR in v.witness


def test_delay_that_still_arrives_before_the_decision_passes(sc, inv):
    root = run(sc)
    rt = next(e.received_time_ns for e in root.views["regional_hq"] if e.id == CORR)
    at = sc.actors[0].decision_rules[0].at_ns
    tr = _delayed(sc, max(0, (at - rt) // S - 1))
    assert tr.decisions[0].payload["option"] == "issue_advisory"
    assert _check(tr, sc, inv)[TARGET].result == Verdict.PASS


def test_fetch_window_and_to_scenario_with_fake_fetcher(tmp_path):
    """The network path, with the HTTP layer replaced: one event, a real-shaped history with a downgrade."""
    summary = {"type": "FeatureCollection", "features": [{
        "id": "xx1", "type": "Feature", "geometry": {"type": "Point", "coordinates": [10.0, 20.0, 5.0]},
        "properties": {"mag": 4.9, "magType": "mb", "place": "test", "time": 1_700_000_010_000,
                       "updated": 1_700_000_600_000, "net": "us",
                       "detail": "https://earthquake.usgs.gov/fdsnws/event/1/query?eventid=xx1&format=geojson"}}]}

    def ver(t, mag, w):
        return {"source": "us", "code": "xx1", "status": "UPDATE", "updateTime": t, "preferredWeight": w,
                "properties": {"magnitude": str(mag), "magnitude-type": "mb", "latitude": "20", "longitude": "10",
                               "depth": "5", "eventtime": "x"}}
    detail = {"properties": {"products": {"origin": [ver(1_700_000_100_000, 5.2, 10), ver(1_700_000_400_000, 4.9, 10)]}}}
    urls = []

    def fake(url):
        urls.append(url)
        return json.dumps(detail if "eventid=" in url else summary).encode()

    raw = usgs.fetch_window("2023-11-14T22:13:00Z", "2023-11-14T22:30:00Z", 4.5, fetcher=fake)
    assert "includesuperseded=true" in urls[1]
    snap = tmp_path / "s.json"
    usgs.snapshot(raw, snap)
    assert usgs.verify_snapshot(snap)
    sc = usgs.to_scenario(snap, tmp_path / "s.yaml", threshold=5.0, decision_at_s=1000)
    kinds = [(i.kind, i.payload["mag"]) for i in sc.injects]
    assert kinds == [("quake_report", 5.2), ("correction", 4.9)]
    assert sc.injects[1].payload["note"] == "downgrade 5.2 -> 4.9"
