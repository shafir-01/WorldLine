"""Delta debugging (Zeller & Hildebrandt 2002) over fault schedules.

Given a failing FaultSchedule and an oracle that says whether a schedule still
produces the target failure, return a 1-minimal schedule: removing any single
intervention makes the failure disappear. Deterministic; every oracle call is
counted and cached so the experiment can report cost honestly.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Hashable, Sequence, TypeVar

T = TypeVar("T", bound=Hashable)
Oracle = Callable[[tuple], bool]  # True = failure still reproduces


@dataclass
class DDMinResult:
    minimal: tuple
    oracle_calls: int
    cache_hits: int
    trace: list[dict] = field(default_factory=list)  # each step: subset size, outcome

    def to_dict(self) -> dict:
        return {
            "minimal": list(self.minimal),
            "minimal_size": len(self.minimal),
            "oracle_calls": self.oracle_calls,
            "cache_hits": self.cache_hits,
            "steps": self.trace,
        }


def ddmin(failing: Sequence[T], oracle: Oracle, *, record_steps: bool = True) -> DDMinResult:
    """Classic ddmin. `oracle(subset)` must be a pure function of the subset.

    Precondition: oracle(tuple(failing)) is True. If it is not, we raise, because
    minimizing a non-failure is a category error the caller must see.
    """
    items = tuple(failing)
    cache: dict[tuple, bool] = {}
    calls = hits = 0
    steps: list[dict] = []

    def test(subset: tuple) -> bool:
        nonlocal calls, hits
        key = tuple(subset)
        if key in cache:
            hits += 1
            return cache[key]
        calls += 1
        out = bool(oracle(key))
        cache[key] = out
        if record_steps:
            steps.append({"size": len(key), "fails": out})
        return out

    if not items:
        return DDMinResult(items, 0, 0, steps)
    if not test(items):
        raise ValueError("ddmin precondition violated: the full schedule does not reproduce the failure")

    n = 2
    while len(items) >= 2:
        chunk = max(1, len(items) // n)
        subsets = [items[i : i + chunk] for i in range(0, len(items), chunk)]
        reduced = False
        # try each subset alone
        for s in subsets:
            if test(s):
                items, n, reduced = s, 2, True
                break
        if reduced:
            continue
        # try each complement
        if n > 2 or len(subsets) > 2:
            for s in subsets:
                comp = tuple(x for x in items if x not in s)
                if comp and test(comp):
                    items, n, reduced = comp, max(n - 1, 2), True
                    break
        if reduced:
            continue
        if n >= len(items):
            break
        n = min(len(items), n * 2)

    return DDMinResult(items, calls, hits, steps)


def one_minimal_check(minimal: Sequence[T], oracle: Oracle) -> bool:
    """Independent witness check: every single removal must make the failure vanish."""
    items = tuple(minimal)
    if not oracle(items):
        return False
    for i in range(len(items)):
        if oracle(items[:i] + items[i + 1 :]):
            return False
    return True
