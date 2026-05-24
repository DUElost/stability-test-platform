"""Centralised job / lease timeout defaults (main-chain fragility §2.4–2.9).

Production defaults are unchanged. Override via env; dev-only shorter defaults
apply when ``ENV`` is set and not ``production`` (and ``TESTING!=1``).

| Variable | Production default | Dev default (optional) |
|----------|-------------------|------------------------|
| ``DISPATCHED_TIMEOUT_SECONDS`` / ``RUN_DISPATCHED_TIMEOUT_SECONDS`` | 120 | 120 |
| ``RUNNING_HEARTBEAT_TIMEOUT_SECONDS`` / ``RUN_HEARTBEAT_TIMEOUT_SECONDS`` | 900 | 900 |
| ``UNKNOWN_GRACE_SECONDS`` | 300 | 300 |
"""

from __future__ import annotations

import os


def _is_non_production_runtime() -> bool:
    env = os.getenv("ENV", "").strip().lower()
    if not env or env == "production":
        return False
    if os.getenv("TESTING") == "1":
        return False
    return True


def _int_env(*names: str, production_default: int, dev_default: int | None = None) -> int:
    for name in names:
        raw = os.getenv(name)
        if raw is not None and raw.strip() != "":
            return int(raw)
    if _is_non_production_runtime() and dev_default is not None:
        return dev_default
    return production_default


# PENDING job never claimed by Agent → recycler marks FAILED
DISPATCHED_TIMEOUT_SECONDS = _int_env(
    "DISPATCHED_TIMEOUT_SECONDS",
    "RUN_DISPATCHED_TIMEOUT_SECONDS",
    production_default=120,
)

# RUNNING job lost heartbeat → UNKNOWN (lease stays; reconciler grace follows)
RUNNING_HEARTBEAT_TIMEOUT_SECONDS = _int_env(
    "RUNNING_HEARTBEAT_TIMEOUT_SECONDS",
    "RUN_HEARTBEAT_TIMEOUT_SECONDS",
    production_default=900,
)

# UNKNOWN job grace before lease release + FAILED
UNKNOWN_GRACE_SECONDS = _int_env(
    "UNKNOWN_GRACE_SECONDS",
    production_default=300,
)
