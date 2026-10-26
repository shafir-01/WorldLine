import pytest

from worldline.core import DeterministicRNG, Event, EventLog, VirtualClock


def ev(i, t=0, **kw):
    return Event(id=f"e{i}", scenario_id="s", source_id="src", event_time_ns=t, kind="k", **kw)


def test_canonical_hash_is_key_order_independent_and_stable():
    a = ev(1, payload={"b": 1, "a": 2})
    b = ev(1, payload={"a": 2, "b": 1})
    assert a.content_hash == b.content_hash
    assert a.content_hash.startswith("sha256:")


def test_receipt_changes_hash_but_not_identity():
    a = ev(1)
    b = a.with_receipt(5)
    assert a.id == b.id and a.content_hash != b.content_hash
    assert a.received_time_ns is None and b.received_time_ns == 5


def test_log_orders_canonically_and_detects_tamper():
    log = EventLog()
    log.extend([ev(2, 20), ev(1, 10)])
    assert [e.id for e in log] == ["e1", "e2"]
    assert log.verify() == []
    log.events[0] = ev(1, 11)
    assert log.verify() == [0]


def test_log_roundtrip_rejects_altered_digest():
    log = EventLog()
    log.extend([ev(1, 1), ev(2, 2)])
    d = log.to_dict()
    assert EventLog.from_dict(d).digest == log.digest
    d["events"][0]["payload"] = {"x": 1}
    with pytest.raises(ValueError):
        EventLog.from_dict(d)


def test_same_seed_same_digest():
    def build(seed):
        r = DeterministicRNG(seed)
        log = EventLog()
        for i in range(50):
            log.append(ev(i, r.randint(0, 10**9)))
        return log.digest

    assert build(7) == build(7)
    assert build(7) != build(8)


def test_rng_fork_is_independent_of_sibling_consumption():
    r = DeterministicRNG(1)
    a1 = r.fork("a").random()
    r.fork("b").random()
    assert a1 == DeterministicRNG(1).fork("a").random()
    assert a1 != DeterministicRNG(1).fork("b").random()


def test_clock_never_rewinds():
    c = VirtualClock()
    c.advance(3)
    assert c.now_ns == 3_000_000_000
    with pytest.raises(ValueError):
        c.advance_to(1)
    assert VirtualClock.restore(c.snapshot()) == c
