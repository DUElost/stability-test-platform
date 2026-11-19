"""#4044 判据面：当前心跳能力证据与 #4043 入场闸门所需的**全量预期 host** 证据。

`partition_capability_evidence` 是本单元向 #4043 提供的接口：一次调用返回该实际
PlanRun × 平台分区的全部预期 host 及每台的判定原因。Owner 裁定 6975368043——入场前
任一台 `capability_unverified` 即硬阻断，因此「列出全部预期 host」与「逐台给出原因」
同样是契约的一部分，不能只回答「有没有不合规的」。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.agent.contracts.capabilities import SCAN_ROUND_V1
from backend.models.host import Device, Host
from backend.models.job import JobInstance
from backend.models.enums import JobStatus
from backend.services import agent_capabilities as caps


def _now():
    return datetime.now(timezone.utc)


def _host(db, host_id, *, status="ONLINE", now=None, capabilities=(SCAN_ROUND_V1,),
          instance="agent-a", reported_at=None, retired_at=None, heartbeat=True):
    host = Host(
        id=host_id, hostname=host_id, status=status,
        last_heartbeat=now if heartbeat else None,
        retired_at=retired_at,
    )
    if capabilities is not None:
        host.agent_capabilities = list(capabilities)
        host.agent_capabilities_reported_at = reported_at if reported_at is not None else now
        host.agent_capabilities_instance_id = instance
    db.add(host)
    db.commit()
    return host


@pytest.fixture
def run_with_hosts(db_session, sample_plan_run, sample_plan):
    """在同一个实际 PlanRun 上按需搭 host × 设备（MTK/UNISOC）矩阵。"""
    created: dict[str, Host] = {}
    counter = {"n": 0}

    def add(host_id, *, platform="MTK", devices=1, **host_kwargs):
        now = host_kwargs.pop("now", None) or _now()
        host = created.get(host_id) or _host(db_session, host_id, now=now, **host_kwargs)
        created[host_id] = host
        for _ in range(devices):
            counter["n"] += 1
            device = Device(
                serial=f"cap-{counter['n']}", host_id=host_id, status="ONLINE",
                platform=platform, adb_connected=True, last_seen=now,
            )
            db_session.add(device)
            db_session.flush()
            db_session.add(JobInstance(
                plan_run_id=sample_plan_run.id, plan_id=sample_plan.id,
                device_id=device.id, host_id=host_id,
                status=JobStatus.COMPLETED.value,
                pipeline_def={"lifecycle": {"init": [], "teardown": []}},
            ))
        db_session.commit()
        return host

    return add, sample_plan_run.id


class TestEvidencePredicate:
    """准入判据的每一行都必须可解释——原因码即审计线索。"""

    def test_current_capability_heartbeat_verifies_host(self, db_session):
        now = _now()
        host = _host(db_session, "h-ok", now=now)
        verdict = caps.evaluate_host_capability(host, now=now)
        assert verdict.verified and verdict.reason is None
        assert verdict.instance_id == "agent-a"

    def test_missing_host_row_fails_closed(self, db_session):
        verdict = caps.evaluate_host_capability(None, now=_now())
        assert not verdict.verified
        assert verdict.reason == caps.REASON_HOST_MISSING

    @pytest.mark.parametrize("case,mutate,reason", [
        ("offline", dict(status="OFFLINE"), caps.REASON_HOST_OFFLINE),
        ("retired", dict(retired_at=_now()), caps.REASON_HOST_RETIRED),
        ("no_heartbeat", dict(heartbeat=False), caps.REASON_HEARTBEAT_MISSING),
        ("never_reported", dict(capabilities=None), caps.REASON_CAPABILITY_NEVER_REPORTED),
        ("empty_capability_list", dict(capabilities=[]), caps.REASON_CAPABILITY_MISSING),
        ("foreign_capability", dict(capabilities=["other_capability"]),
         caps.REASON_CAPABILITY_MISSING),
        ("instance_unbound", dict(instance=""), caps.REASON_INSTANCE_UNBOUND),
        ("superseded_by_newer_beat",
         dict(reported_at=_now() - timedelta(seconds=1)),
         caps.REASON_CAPABILITY_NOT_CURRENT),
    ])
    def test_each_negative_row_has_a_stable_reason(self, db_session, case, mutate, reason):
        now = _now()
        host = _host(db_session, f"h-{case}", now=now, **mutate)
        verdict = caps.evaluate_host_capability(host, now=now)
        assert not verdict.verified
        assert verdict.reason == reason

    def test_liveness_is_delegated_to_the_existing_online_fact(self, db_session):
        """存活判定沿用 ``Host.status``（session watchdog 维护），这里不重算墙钟。

        一台「ONLINE 但心跳已停」的机器在 watchdog 判 OFFLINE 之前仍可被预约；
        这与 #3875 已复核的 digest 预筛同口径。真正的兜底是发送侧：Agent 不在收
        不到，ACK 缺失记 no-ack，能力证据缺失记 capability_unverified。
        """
        quiet = _now() - timedelta(seconds=10_000)
        host = _host(db_session, "h-quiet-online", now=quiet, reported_at=quiet)
        assert caps.evaluate_host_capability(host, now=_now()).verified
        host.status = "OFFLINE"
        db_session.commit()
        verdict = caps.evaluate_host_capability(host, now=_now())
        assert not verdict.verified and verdict.reason == caps.REASON_HOST_OFFLINE

    def test_evidence_not_superseded_by_a_later_beat(self, db_session):
        """6704562784 口径：只要**没有更新的心跳**改写事实，上一拍证明依然成立；
        反过来，更新的一拍若不声明能力就立即失效（不会「保留摘要」式沿用）。"""
        early = _now()
        host = _host(db_session, "h-order", now=early, reported_at=early)
        assert caps.evaluate_host_capability(host, now=early).verified

        host.last_heartbeat = early + timedelta(seconds=30)
        db_session.commit()
        verdict = caps.evaluate_host_capability(host, now=early + timedelta(seconds=30))
        assert not verdict.verified
        assert verdict.reason == caps.REASON_CAPABILITY_NOT_CURRENT

    def test_required_capability_can_be_named_by_the_caller(self, db_session):
        now = _now()
        host = _host(db_session, "h-required", now=now)
        assert caps.evaluate_host_capability(host, now=now, required="future_round_v2").reason \
            == caps.REASON_CAPABILITY_MISSING


class TestPartitionEvidenceForAdmission:
    """#4043 入场闸门消费的接口：同一实际 PlanRun × MTK 分区的全部预期 host。"""

    def test_lists_every_expected_host_and_admits_when_all_verified(self, db_session, run_with_hosts):
        add, run_id = run_with_hosts
        for host_id in ("p-a", "p-b", "p-c"):
            add(host_id)
        evidence = caps.partition_capability_evidence(
            db_session, plan_run_id=run_id, platform="mtk")
        assert [host.host_id for host in evidence.hosts] == ["p-a", "p-b", "p-c"]
        assert evidence.admitted
        assert evidence.unverified == ()

    def test_one_unverified_expected_host_blocks_admission(self, db_session, run_with_hosts):
        add, run_id = run_with_hosts
        now = _now()
        add("p-ok-1", now=now)
        add("p-old-build", capabilities=[], instance="agent-old", now=now)
        add("p-ok-2", now=now)
        evidence = caps.partition_capability_evidence(db_session, plan_run_id=run_id, platform="mtk")

        assert not evidence.admitted
        assert [host.host_id for host in evidence.unverified] == ["p-old-build"]
        assert evidence.unverified[0].reason == caps.REASON_CAPABILITY_MISSING
        # 其余两台的证明仍然成立：闸门是「全量核验」，不是「任意一台」——
        # #4043 需要按 host 写缺失登记，必须知道到底缺了谁。
        verified = {host.host_id for host in evidence.hosts if host.verified}
        assert verified == {"p-ok-1", "p-ok-2"}

    def test_offline_and_retired_expected_hosts_block_admission(
        self, db_session, run_with_hosts,
    ):
        add, run_id = run_with_hosts
        now = _now()
        add("p-online-ok", now=now)
        add("p-offline", status="OFFLINE", now=now)
        add("p-retired", retired_at=now, now=now)
        evidence = caps.partition_capability_evidence(db_session, plan_run_id=run_id, platform="mtk")
        reasons = {host.host_id: host.reason for host in evidence.unverified}
        assert reasons == {"p-offline": caps.REASON_HOST_OFFLINE,
                           "p-retired": caps.REASON_HOST_RETIRED}
        assert not evidence.admitted

    def test_scope_is_the_partition_not_the_whole_plan(self, db_session, run_with_hosts):
        """同 run 内的展锐-only host 不算 MTK 预期 host（ADR-0032 B1 分区口径），
        否则 #4043 会被一台永远不会产出 MTK 产物的机器硬阻断。"""
        add, run_id = run_with_hosts
        now = _now()
        add("p-mtk", now=now)
        add("p-unisoc-only", platform="UNISOC", now=now)
        mtk = caps.partition_capability_evidence(db_session, plan_run_id=run_id, platform="mtk")
        unisoc = caps.partition_capability_evidence(
            db_session, plan_run_id=run_id, platform="unisoc")
        assert [host.host_id for host in mtk.hosts] == ["p-mtk"]
        assert [host.host_id for host in unisoc.hosts] == ["p-unisoc-only"]
        # 两台都上报了能力：分区口径只改变「谁在该分区的期望集里」，不改变判定。
        assert mtk.admitted and unisoc.admitted

    def test_host_without_devices_is_not_expected(self, db_session, run_with_hosts):
        add, run_id = run_with_hosts
        now = _now()
        add("p-with-devices", now=now)
        add("p-no-devices", now=now)
        db_session.query(JobInstance).filter(JobInstance.host_id == "p-no-devices").delete()
        db_session.commit()
        evidence = caps.partition_capability_evidence(db_session, plan_run_id=run_id, platform="mtk")
        assert [host.host_id for host in evidence.hosts] == ["p-with-devices"]

    def test_serialisable_shape_is_stable_for_the_admission_ledger(
        self, db_session, run_with_hosts,
    ):
        add, run_id = run_with_hosts
        add("p-shape", capabilities=[])
        payload = caps.partition_capability_evidence(
            db_session, plan_run_id=run_id, platform="mtk").as_dict()
        assert payload["plan_run_id"] == run_id and payload["platform"] == "mtk"
        assert payload["admitted"] is False
        host, = payload["hosts"]
        assert set(host) == {
            "host_id", "verified", "reason", "status", "capabilities", "instance_id",
            "heartbeat_at", "capability_reported_at", "evaluated_at", "retired",
        }
        assert host["host_id"] == "p-shape" and host["verified"] is False
