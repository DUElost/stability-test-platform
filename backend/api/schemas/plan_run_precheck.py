"""ADR-0021 — PlanRun.run_context.precheck Pydantic schema.

This describes the shape persisted into ``PlanRun.run_context.precheck``
during the dispatch gate.  Frontend type generation reads these classes
to render the dispatch gate progress card.

The structure is intentionally a strict subset that the gate writer
(C3) and the frontend reader (C5) agree on.  ``run_context`` is JSONB,
so older PlanRuns may still have ``run_context.precheck = None``.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


PrecheckPhase = Literal["verifying", "syncing", "reverifying", "ready", "failed"]
PrecheckHostStatus = Literal["pending", "ok", "syncing", "synced", "failed"]
PrecheckFinalResult = Literal["ready", "failed", "aborted"]


class PrecheckScriptResult(BaseModel):
    """One row in ``hosts[host_id].scripts`` — per-script verification record."""

    model_config = ConfigDict(extra="forbid")

    name: str
    version: str
    expected_sha: str
    actual_sha: Optional[str] = None
    exists: bool = False
    ok: bool = False
    error: Optional[str] = None


class PrecheckHostState(BaseModel):
    """One entry in ``hosts`` — per-host alignment record."""

    model_config = ConfigDict(extra="forbid")

    status: PrecheckHostStatus = "pending"
    checked_at: Optional[str] = None
    synced_at: Optional[str] = None
    scripts: list[PrecheckScriptResult] = Field(default_factory=list)
    sync_attempts: int = 0
    error: Optional[str] = None


class PrecheckSummary(BaseModel):
    """Top-level ``run_context.precheck`` payload.

    Lives under ``PlanRun.run_context['precheck']``.  When the dispatch
    gate finishes, ``final_result`` and ``completed_at`` are set; while
    in flight, ``phase`` reflects the active stage.
    """

    model_config = ConfigDict(extra="forbid")

    phase: PrecheckPhase = "verifying"
    started_at: str
    completed_at: Optional[str] = None
    hosts: dict[str, PrecheckHostState] = Field(default_factory=dict)
    final_result: Optional[PrecheckFinalResult] = None
    errors: list[str] = Field(default_factory=list)


__all__ = [
    "PrecheckPhase",
    "PrecheckHostStatus",
    "PrecheckFinalResult",
    "PrecheckScriptResult",
    "PrecheckHostState",
    "PrecheckSummary",
]
