"""Deterministic, forkable PRNG. Every branch derives its stream from (seed, branch_path).

Forking by label means a sibling consuming randomness never perturbs another branch:
the whole exploration tree is reproducible from one seed.
"""
from __future__ import annotations

import hashlib
import random
from typing import Sequence, TypeVar

T = TypeVar("T")


class DeterministicRNG:
    def __init__(self, seed: int | str, path: tuple[str, ...] = ()):
        self.seed = seed
        self.path = tuple(path)
        material = f"{seed}|{'/'.join(self.path)}".encode()
        self._r = random.Random(int.from_bytes(hashlib.sha256(material).digest()[:8], "big"))

    def fork(self, label: str) -> "DeterministicRNG":
        return DeterministicRNG(self.seed, self.path + (label,))

    def randint(self, a: int, b: int) -> int:
        return self._r.randint(a, b)

    def random(self) -> float:
        return self._r.random()

    def choice(self, seq: Sequence[T]) -> T:
        return self._r.choice(seq)

    def sample(self, seq: Sequence[T], k: int) -> list[T]:
        return self._r.sample(list(seq), k)

    def shuffle(self, seq: Sequence[T]) -> list[T]:
        out = list(seq)
        self._r.shuffle(out)
        return out

    def gauss(self, mu: float, sigma: float) -> float:
        return self._r.gauss(mu, sigma)

    def describe(self) -> dict:
        return {"seed": self.seed, "path": list(self.path)}
