"""Agent-local cumulative timing buckets transported by the next heartbeat."""

from __future__ import annotations

import math

from .contracts.heartbeat_timing import BUCKETS_SECONDS, PHASES, SNAPSHOT_VERSION


class HeartbeatTiming:
    """Fixed vocabulary and buckets; no per-device series or unbounded samples."""

    def __init__(self) -> None:
        self._counts: dict[str, list[int]] = {}
        self._sums: dict[str, float] = {}

    def observe(self, phase: str, seconds: float) -> None:
        if phase not in PHASES:
            raise ValueError(f"unknown heartbeat timing phase: {phase}")
        value = float(seconds)
        if not math.isfinite(value) or value < 0:
            return
        counts = self._counts.setdefault(phase, [0] * (len(BUCKETS_SECONDS) + 1))
        for index, upper_bound in enumerate(BUCKETS_SECONDS):
            if value <= upper_bound:
                counts[index] += 1
        counts[-1] += 1  # +Inf
        self._sums[phase] = self._sums.get(phase, 0.0) + value

    def snapshot(self, *, slow_due: int, disk_due: int) -> dict:
        return {
            "version": SNAPSHOT_VERSION,
            "phases": {
                phase: {
                    "buckets": list(self._counts[phase]),
                    "count": self._counts[phase][-1],
                    "sum": self._sums[phase],
                }
                for phase in PHASES if phase in self._counts
            },
            "slow_due": max(0, int(slow_due)),
            "disk_due": max(0, int(disk_due)),
        }
