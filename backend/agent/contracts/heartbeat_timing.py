"""Bounded heartbeat timing vocabulary shared by Agent and control plane (#3219)."""

from __future__ import annotations

# One observation per tick and phase. Probe stages are the slowest individual
# device in that tick; ``probe_total`` includes pool waves and scheduler waits.
PHASES = (
    "tick_total",
    "tick_interval",
    "discover",
    "probe_total",
    "probe_fast_max",
    "probe_slow_max",
    "disk_sample",
    "prepare",
    "http",
    "reconnect",
)

# The 25-device case can run several eight-device waves; keep the slow tail
# visible instead of putting every long tick into +Inf.
BUCKETS_SECONDS = (
    0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0,
    2.5, 5.0, 10.0, 20.0, 40.0, 80.0, 160.0, 320.0,
)

SNAPSHOT_VERSION = 1
