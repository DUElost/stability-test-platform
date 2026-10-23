"""#3723 U3 SourceGuard: JOB lease release must go through device_return entry.

Anchors prove the five modules still contain the historical call sites;
form asserts forbid direct ``release_lease`` / ``release_lease_sync`` outside
the unified entry (and lease_manager primitives themselves).
"""

from __future__ import annotations

import pytest

from tools.dev.source_anchor import SourceGuard

_MODULES = [
    (
        "backend/services/agent_completion.py",
        "handle_job_lease_release(",
        1,
    ),
    (
        "backend/services/agent_recovery.py",
        "handle_job_lease_release(",
        3,
    ),
    (
        "backend/scheduler/device_lease_reconciler.py",
        "handle_job_lease_release(",
        5,
    ),
    (
        "backend/scheduler/recycler.py",
        "handle_job_lease_release_sync(",
        1,
    ),
    (
        "backend/services/device_lease_emergency_release.py",
        "handle_job_lease_release_sync(",
        1,
    ),
]


@pytest.mark.parametrize("rel,anchor,expect", _MODULES)
def test_u3_job_lease_release_routed_through_device_return(rel, anchor, expect):
    guard = SourceGuard.of_repo_path(rel).anchored(anchor, expect=expect)
    guard.assert_absent(
        "await release_lease(",
        why="#3723 §3.4 / §1.2：JOB 租约不得再直接 await release_lease",
    )
    guard.assert_absent(
        "release_lease_sync(",
        why="#3723 §3.4 / §1.2：JOB 租约不得再直接 release_lease_sync",
    )


def test_u3_eleven_call_sites_total():
    total = sum(expect for _, _, expect in _MODULES)
    assert total == 11


def test_lease_manager_primitives_still_exist():
    """Primitives remain for the unified entry only."""
    guard = SourceGuard.of_repo_path("backend/services/lease_manager.py")
    guard.anchored("async def release_lease(")
    guard.anchored("def release_lease_sync(")
