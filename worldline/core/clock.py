"""VirtualClock: explicit epoch, integer nanoseconds, never reads wall time."""
from __future__ import annotations

from dataclasses import dataclass

NS_PER_S = 1_000_000_000


@dataclass
class VirtualClock:
    epoch_ns: int = 0
    tick_ns: int = NS_PER_S
    now_ns: int = 0

    def __post_init__(self) -> None:
        if self.now_ns < self.epoch_ns:
            self.now_ns = self.epoch_ns

    def advance(self, ticks: int = 1) -> int:
        if ticks < 0:
            raise ValueError("virtual clock does not go backwards; branch from a snapshot instead")
        self.now_ns += ticks * self.tick_ns
        return self.now_ns

    def advance_to(self, t_ns: int) -> int:
        if t_ns < self.now_ns:
            raise ValueError(f"cannot move clock from {self.now_ns} back to {t_ns}")
        self.now_ns = t_ns
        return self.now_ns

    def snapshot(self) -> dict:
        return {"epoch_ns": self.epoch_ns, "tick_ns": self.tick_ns, "now_ns": self.now_ns}

    @staticmethod
    def restore(s: dict) -> "VirtualClock":
        return VirtualClock(**s)
