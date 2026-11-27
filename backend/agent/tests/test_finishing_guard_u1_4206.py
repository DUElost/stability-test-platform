"""B12 U1 / ADR-0065：FinishingGuard 契约纯函数（C1 多主体合并 + C2 gpu_finish）。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.agent.contracts.capabilities import (
    AGENT_CAPABILITIES,
    ATOMIC_COMMIT,
    FINISHING_GUARD_V2,
    FORCE_ABORT,
)
from backend.agent.contracts.device_return import freeze_disarm_guard, receive_disarm_evidence
from backend.agent.contracts.finishing_guard import (
    ABORT_INTENSITY_FORCE,
    ABORT_INTENSITY_GRACEFUL,
    FINISHING_KIND_ATOMIC_COMMIT,
    FINISHING_KIND_TEARDOWN_DISARM,
    GUARD_STATE_GRANTED,
    GUARD_STATE_PRE_REGISTERED,
    coerce_job_finishing_guard,
    effective_abort_force,
    finishing_guard_deadline,
    finishing_guard_expiry,
    finishing_kind_of,
    is_clock_regressed,
    max_abort_intensity,
    min_abort_clock,
    wrap_disarm_as_finishing_guard,
)


GPU_FINISH_STEP = {
    "step_id": "gpu_fin",
    "action": "script:gpu_finish",
    "version": "1.1.0",
    "timeout_seconds": 600,
    "package_sha256": "a" * 64,
}


def _gpu_execute_guard(*, now: float = 0.0):
    g = freeze_disarm_guard(
        [GPU_FINISH_STEP], now=now, abort_at=now,
        fencing_token="tok", agent_instance_id="inst",
    )
    evidence = {
        "step_id": "gpu_fin", "seq": 1, "phase": "execute", "progress_seq": 1,
        "script_name": "gpu_finish", "script_version": "1.1.0",
        "package_sha256": "a" * 64,
    }
    return receive_disarm_evidence(g, evidence, now=now, via_coordinator=True)


def test_capability_constants_registered_but_not_in_agent_heartbeat_set():
    assert FINISHING_GUARD_V2 == "finishing_guard_v2"
    assert ATOMIC_COMMIT == "atomic_commit"
    assert FORCE_ABORT == "force_abort"
    # U1 不得提前开启心跳上报（U5 完备后再并入 AGENT_CAPABILITIES）
    assert FINISHING_GUARD_V2 not in AGENT_CAPABILITIES
    assert ATOMIC_COMMIT not in AGENT_CAPABILITIES
    assert FORCE_ABORT not in AGENT_CAPABILITIES


def test_c1_multi_subject_intensity_max_and_clock_min():
    assert max_abort_intensity(False, False) == ABORT_INTENSITY_GRACEFUL
    assert max_abort_intensity(False, True) == ABORT_INTENSITY_FORCE
    assert max_abort_intensity(ABORT_INTENSITY_GRACEFUL, ABORT_INTENSITY_FORCE) == (
        ABORT_INTENSITY_FORCE
    )
    early = datetime(2026, 11, 27, 10, 0, tzinfo=timezone.utc)
    late = early + timedelta(seconds=120)
    assert min_abort_clock(late, early, None) == early
    assert min_abort_clock(None, None) is None
    # B7：naive 归一化为 UTC-aware，不与 aware 混比抛 TypeError
    naive = datetime(2026, 11, 27, 9, 59, 0)
    assert min_abort_clock(early, naive) == naive.replace(tzinfo=timezone.utc)
    ctx = {
        "abort_requested": {
            "at": early.isoformat(), "force": True,
            "requested_job_ids": [1],
        },
        "abort_requested_hosts": {
            "h1": {"at": late.isoformat(), "force": False},
        },
    }
    # Job 1 同时被 run(force) 与 host 覆盖 → force
    assert effective_abort_force(ctx, 1, "h1") is True
    # B5：Job 2 不在 run 名单内，不得继承 run 级 force=true
    assert effective_abort_force(ctx, 2, "h1") is False
    assert effective_abort_force(ctx, 2, "h-missing") is False


def test_legacy_disarm_guard_absorbed_as_teardown_disarm():
    wire = _gpu_execute_guard()
    assert finishing_kind_of(wire) == FINISHING_KIND_TEARDOWN_DISARM
    wrapped = wrap_disarm_as_finishing_guard(wire, trigger="manual_abort")
    assert wrapped["kind"] == FINISHING_KIND_TEARDOWN_DISARM
    assert wrapped["trigger"] == "manual_abort"
    coerced = coerce_job_finishing_guard(None, wire)
    assert coerced is not None and coerced["kind"] == FINISHING_KIND_TEARDOWN_DISARM


def test_b1_dual_column_prefers_fresher_seq_and_absorbs_regression():
    stale = wrap_disarm_as_finishing_guard(_gpu_execute_guard(now=1000.0))
    live = receive_disarm_evidence(
        _gpu_execute_guard(now=1000.0),
        {
            "step_id": "gpu_fin", "seq": 5, "phase": "execute", "progress_seq": 5,
            "script_name": "gpu_finish", "script_version": "1.1.0",
            "package_sha256": "a" * 64,
        },
        now=1350.0,
        via_coordinator=True,
    )
    picked = coerce_job_finishing_guard(stale, live)
    assert picked is not None and picked["seq"] == 5
    # 任一列 clock_regressed → 吸收失效
    regressed_live = {**live, "reason": "clock_regressed"}
    dead = coerce_job_finishing_guard(stale, regressed_live)
    assert dead is not None and is_clock_regressed(dead)
    # 标准字段单独存在亦吸收
    canon = {**stale, "guard_invalidated_reason": "clock_regressed"}
    dead2 = coerce_job_finishing_guard(canon, live)
    assert dead2 is not None and is_clock_regressed(dead2)


def test_b2_canonical_regression_expires_teardown_guard():
    g = wrap_disarm_as_finishing_guard(_gpu_execute_guard(now=1000.0))
    g = {**g, "guard_invalidated_reason": "clock_regressed"}
    assert finishing_guard_deadline(
        g, now=1010.0, owner_valid=True, execution_heartbeat_at=1010.0,
    ) is None
    assert finishing_guard_expiry(g) == float(g["t0"])


def test_c2_gpu_finish_600s_protected_past_300s_unknown_grace():
    """#3867 形态：teardown_disarm + 新鲜证据时，超过 300s 仍受保护。"""
    t0 = 1_000.0
    guard = wrap_disarm_as_finishing_guard(_gpu_execute_guard(now=t0))
    # 刷新证据到 t0+350（超过 UNKNOWN_GRACE=300，仍在 600s 步骤预算内）
    at = t0 + 350
    fresh = receive_disarm_evidence(
        guard,
        {
            "step_id": "gpu_fin", "seq": 2, "phase": "execute", "progress_seq": 2,
            "script_name": "gpu_finish", "script_version": "1.1.0",
            "package_sha256": "a" * 64,
        },
        now=at,
        via_coordinator=True,
    )
    deadline = finishing_guard_deadline(
        fresh, now=at, owner_valid=True, execution_heartbeat_at=at,
    )
    assert deadline is not None
    assert deadline > at


def test_c2_exits_when_stale_or_clock_regressed_or_past_step():
    t0 = 1_000.0
    guard = wrap_disarm_as_finishing_guard(_gpu_execute_guard(now=t0))
    # 证据失鲜（>60s）
    assert finishing_guard_deadline(
        guard, now=t0 + 90, owner_valid=True, execution_heartbeat_at=t0 + 90,
    ) is None
    # 时钟回拨失效
    regressed = {**guard, "reason": "clock_regressed"}
    assert finishing_guard_deadline(
        regressed, now=t0 + 10, owner_valid=True, execution_heartbeat_at=t0 + 10,
    ) is None
    # 超过步骤 / job 截止
    assert finishing_guard_deadline(
        guard, now=t0 + 10_000, owner_valid=True, execution_heartbeat_at=t0 + 10_000,
    ) is None


def test_atomic_commit_branch_pre_registered_and_granted():
    now = 5_000.0
    pre = {
        "kind": FINISHING_KIND_ATOMIC_COMMIT,
        "guard_state": GUARD_STATE_PRE_REGISTERED,
        "D_atomic": now + 600,
        "permit_expires_at": now + 30,
        "last_liveness_receipt_at": now,
        "last_progress_receipt_at": now,
        "last_server_at": now,
    }
    assert finishing_guard_deadline(pre, now=now + 10, owner_valid=True, execution_heartbeat_at=None)
    assert finishing_guard_deadline(pre, now=now + 31, owner_valid=True, execution_heartbeat_at=None) is None

    granted = {
        **pre,
        "guard_state": GUARD_STATE_GRANTED,
        "permit_expires_at": now + 30,
        "last_liveness_receipt_at": now,
        "last_progress_receipt_at": now,
    }
    assert finishing_guard_deadline(
        granted, now=now + 15, owner_valid=True, execution_heartbeat_at=None,
    )
    # 存活窗过期
    assert finishing_guard_deadline(
        granted, now=now + 61, owner_valid=True, execution_heartbeat_at=None,
    ) is None
