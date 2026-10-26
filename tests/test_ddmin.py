import pytest

from worldline.explorer.ddmin import ddmin, one_minimal_check


def test_single_culprit_found_among_noise():
    culprit = {"d7"}
    oracle = lambda s: culprit <= set(s)
    r = ddmin(tuple(f"d{i}" for i in range(20)), oracle)
    assert set(r.minimal) == culprit
    assert one_minimal_check(r.minimal, oracle)
    assert r.oracle_calls < 40


def test_two_interacting_culprits():
    need = {"a", "k"}
    oracle = lambda s: need <= set(s)
    r = ddmin(tuple("abcdefghijklmnop"), oracle)
    assert set(r.minimal) == need
    assert one_minimal_check(r.minimal, oracle)


def test_rejects_non_failing_input():
    with pytest.raises(ValueError):
        ddmin(("x", "y"), lambda s: False)


def test_everything_needed_is_already_minimal():
    oracle = lambda s: len(s) == 3
    r = ddmin(("x", "y", "z"), oracle)
    assert set(r.minimal) == {"x", "y", "z"}
    assert one_minimal_check(r.minimal, oracle)


def test_deterministic_and_cached():
    oracle = lambda s: "m" in s
    r1 = ddmin(tuple("abcdefghijklmnopqrstuvwxyz"), oracle)
    r2 = ddmin(tuple("abcdefghijklmnopqrstuvwxyz"), oracle)
    assert r1.minimal == r2.minimal == ("m",)
    assert r1.oracle_calls == r2.oracle_calls
