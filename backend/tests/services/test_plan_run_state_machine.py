"""Unit tests for PlanRunStateMachine (issue #49).

Mirrors the style of JobStateMachine tests: pure in-memory SimpleNamespace
stand-ins for PlanRun, no DB required.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.models.enums import PlanRunStatus
from backend.services.state_machine import InvalidTransitionError, PlanRunStateMachine


def _run(status: PlanRunStatus) -> SimpleNamespace:
    return SimpleNamespace(id=1, status=status.value)


@pytest.mark.parametrize(
    "target",
    [
        PlanRunStatus.SUCCESS,
        PlanRunStatus.PARTIAL_SUCCESS,
        PlanRunStatus.FAILED,
    ],
)
def test_running_can_transition_to_any_terminal_status(target):
    run = _run(PlanRunStatus.RUNNING)
    PlanRunStateMachine.transition(run, target)
    assert run.status == target.value


def test_running_cannot_transition_to_degraded():
    """DEGRADED 是历史可读终态，但新聚合不得再生产该状态。"""
    run = _run(PlanRunStatus.RUNNING)
    with pytest.raises(InvalidTransitionError):
        PlanRunStateMachine.transition(run, PlanRunStatus.DEGRADED)
    assert run.status == PlanRunStatus.RUNNING.value


def test_failed_can_retry_back_to_queued():
    run = _run(PlanRunStatus.FAILED)
    PlanRunStateMachine.transition(run, PlanRunStatus.QUEUED, reason="dispatch_retry")
    assert run.status == PlanRunStatus.QUEUED.value


@pytest.mark.parametrize(
    "terminal",
    [
        PlanRunStatus.SUCCESS,
        PlanRunStatus.PARTIAL_SUCCESS,
        PlanRunStatus.DEGRADED,
    ],
)
def test_success_partial_and_degraded_are_terminal(terminal):
    run = _run(terminal)
    with pytest.raises(InvalidTransitionError):
        PlanRunStateMachine.transition(run, PlanRunStatus.RUNNING)
    assert run.status == terminal.value


def test_failed_cannot_go_directly_to_success():
    run = _run(PlanRunStatus.FAILED)
    with pytest.raises(InvalidTransitionError):
        PlanRunStateMachine.transition(run, PlanRunStatus.SUCCESS)
    assert run.status == PlanRunStatus.FAILED.value


def test_running_cannot_self_loop_via_state_machine():
    """RUNNING->RUNNING (precheck retry idempotent reset) is intentionally
    NOT a registered transition — callers must bypass the state machine for
    that case rather than relying on a self-loop (see precheck/runner.py)."""
    run = _run(PlanRunStatus.RUNNING)
    with pytest.raises(InvalidTransitionError):
        PlanRunStateMachine.transition(run, PlanRunStatus.RUNNING)


def test_unknown_current_status_raises():
    run = _run(PlanRunStatus.RUNNING)
    run.status = "BOGUS"
    with pytest.raises(InvalidTransitionError):
        PlanRunStateMachine.transition(run, PlanRunStatus.FAILED)
