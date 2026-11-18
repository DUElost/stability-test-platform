"""B8 #4045 阶段二前置取证（基线主干 ``e7325af03``，不依赖任何未合入实现）。

按 #4045「真实入口与验收」第 1 条前半与 #3800 §2.1 Owner 裁定第 2 条取四条判据：

- **P1 基线只能登记缺源**：仅 Agent 持有 I4 期内原件（DLE 非 ``PRUNED``、
  ``remote_path`` 为空、中心无 ``VERIFIED`` 发布、无 HddSpill 副本）时，真实补跑路由
  ``POST /api/v1/plan-runs/{run_id}/dedup/compat-rerun`` 把这些发生登记为缺源
  （``missing_items.reason=missing_after_prune_or_unavailable``、``is_partial=True``），
  ``compat_reruns/{run}/{platform}/r{id}/events/`` 下不产生任何副本。
- **P2 缺的是入口接线，不是复制能力**：同一份数据在服务级显式给出
  ``local_source_roots`` 时，同一发生即按 ``agent_local_original`` 取得并落隔离目录。
- **P3 G6 通路不能原样复用**：``ensure_partition_publications`` 只覆盖 S7 已选代表，
  Agent 待办里没有非代表发生；即使强行替非代表登记发布记录，真实回执服务仍按
  ``EVIDENCE_NOT_REPRESENTATIVE`` 拒绝；目的路径契约只接受 ``evidence/`` 前缀。
- **P4 可复用内核与族无关**：G6 的「无 symlink 复制 + 复制后按字节重算哈希复核」
  对 ``compat_reruns/`` 隔离目录同样成立，越根与 symlink 一律拒绝。

文件系统在临时根、DB 为隔离 PG16；P1/P2/P3 的补跑走真实 HTTP 路由（每请求独立会话，
对齐生产 ``get_db``），G6 侧走真实服务函数与真实路径契约函数。
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.agent.contracts.evidence_layout import (
    EvidenceLayoutError,
    copytree_no_symlinks,
    dir_sha256_and_size,
    evidence_rel_path,
    parse_evidence_rel_path,
)
from backend.core.artifact_paths import ArtifactPathError, resolve_evidence_event_path
from backend.core.database import get_db
from backend.main import fastapi_app as app
from backend.models.device_log_event import DeviceLogEvent
from backend.services import compat_rerun as compat_module
from backend.services import evidence_publication as g6
from backend.services import issue_fact_store as facts
from backend.tests.services.test_b8_g8_compat_rerun import (
    COLLECTION_FOLDER,
    HOST,
    PLATFORM,
    _dle,
    _event_dir,
    _event_name,
    _read_report,
    _run,
    _seed_native_round,
    _version,
    # pytest 夹具按本模块命名空间解析，compat_env 必须可被 route_env 依名取到。
    compat_env,  # noqa: F401
)

ROUTE = "/api/v1/plan-runs/{run_id}/dedup/compat-rerun"


@pytest.fixture
def route_env(compat_env, client, admin_headers):
    """真实路由 + 每请求独立 PG 会话 + 台账观测会话（复用 G8 探针姿势）。"""
    factory, root = compat_env

    def _fresh_session_db():
        db = factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _fresh_session_db
    yield client, factory, root, admin_headers
    app.dependency_overrides.pop(get_db, None)


def _agent_local(root: Path, serial: str, event: str) -> Path:
    """Agent 主机上仍存在的 I4 期内原件（中心无副本时唯一来源）。"""
    return _event_dir(root / "agent-local", COLLECTION_FOLDER, serial, "aee_exp", event)


def _non_representative_occurrence(db, run, *, serial, event, local_path, signature):
    """同问题的第二次发生：有 DLE 与 occurrence，但**不是** S7 代表。"""
    row = DeviceLogEvent(
        serial=serial, platform="MTK", event_type="JE", state="LOCAL",
        local_path=str(local_path), remote_path=None, host_id=HOST,
        plan_run_id=run.id, occurrence_key=f"{serial}-{event}",
        detected_at=datetime(2026, 11, 16, 10, 0, tzinfo=timezone.utc),
    )
    db.add(row)
    db.flush()
    occurrence = facts.record_issue_occurrence(
        db, signature_id=signature.id, occurrence_key=row.occurrence_key,
        serial=serial, observed_at=row.detected_at, first_seen_source="local",
        first_seen_plan_run_id=run.id, build_version="V1",
    )
    facts.link_occurrence_event(
        db, occurrence_id=occurrence.id, dle_id=row.id, signature_id=signature.id,
    )
    db.flush()
    return row


def _seed_agent_only(factory, root):
    """一个问题两个发生，原件都只在 Agent 本地；A 是代表、B 不是。

    返回 ``(run_id, a_id, b_id, agent_root)``。中心侧刻意不建任何材料：没有
    ``EvidencePublication``、``remote_path`` 为空、没有 ``SpillLedger``。
    """
    with factory() as db:
        run = _run(db)
        ev_a, ev_b = _event_name(), _event_name()
        serial_a, serial_b = "SN-REP-LOCAL", "SN-NONREP-LOCAL"
        a, sig_a, _occ = _dle(
            db, run, serial=serial_a, event=ev_a, state="LOCAL",
            local_path=str(_agent_local(root, serial_a, ev_a)), remote_path=None,
        )
        b = _non_representative_occurrence(
            db, run, serial=serial_b, event=ev_b,
            local_path=_agent_local(root, serial_b, ev_b), signature=sig_a,
        )
        round_id = _seed_native_round(db, root, run, [a, b])
        _version(db, run, no=1, issue_ids=[sig_a.id], event_ids=[a.id, b.id], round_id=round_id)
        db.commit()
        return int(run.id), str(a.id), str(b.id), root / "agent-local"


def _trigger(client, admin_headers, run_id, **params):
    resp = client.post(ROUTE.format(run_id=run_id), params=params, headers=admin_headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def _copy_ledger(factory, run_id, *, request_key):
    with factory() as db:
        return facts.get_compat_rerun_by_request(
            db, plan_run_id=run_id, platform=PLATFORM, request_key=request_key,
        )


def test_p1_route_baseline_registers_missing_source_for_agent_only_originals(route_env):
    """P1：真实补跑路由在「只有 Agent 持有原件」时只能登记缺源，且零副本产出。"""
    client, factory, root, admin_headers = route_env
    run_id, a_id, b_id, _agent_root = _seed_agent_only(factory, root)

    data = _trigger(
        client, admin_headers, run_id, request_key="p1-baseline", delivery_version_no=1,
    )
    rerun = data["rerun"]

    assert data["replayed"] is False
    assert sorted(item["dle_id"] for item in rerun["missing_items"]) == sorted([a_id, b_id])
    assert {item["reason"] for item in rerun["missing_items"]} == {
        "missing_after_prune_or_unavailable",
    }
    assert rerun["source_records"] == []
    assert rerun["is_partial"] is True
    assert rerun["coverage_scope"]["covered_events"] == 0
    assert rerun["coverage_scope"]["missing_events"] == 2
    # 缺源不阻断登记（D15）：仍按部分成果 COMPLETED，XLS 的 Path 为空。
    assert rerun["status"] == facts.COMPAT_RERUN_STATUS_COMPLETED
    rows = _read_report(rerun["merge_xls_path"])
    assert len(rows) == 1 and rows[0]["Path"] == ""
    assert rows[0]["Count"] == 2 and rows[0]["DeviceCount"] == 2

    # 隔离目录里没有任何副本，原件仍只在 Agent 侧。
    events_dir = Path(rerun["artifact_dir"]) / "events"
    assert events_dir.is_dir()
    assert [p for p in events_dir.rglob("*") if p.is_file()] == []
    assert {p.name for p in Path(rerun["artifact_dir"]).iterdir()} <= {
        "compat_manifest.json", "events", Path(rerun["merge_xls_path"]).name,
    }
    ledger = _copy_ledger(factory, run_id, request_key="p1-baseline")
    assert ledger is not None and ledger.missing_items == rerun["missing_items"]
    assert (root / "agent-local").is_dir()


def test_p2_service_level_local_source_roots_would_obtain_the_same_occurrence(route_env):
    """P2：同一发生只差「入口能否给出 local_source_roots」，复制算法本来就在。"""
    client, factory, root, admin_headers = route_env
    run_id, a_id, b_id, agent_root = _seed_agent_only(factory, root)

    # 路由侧：没有任何参数可以让它读 Agent 本地根（生产入口未服务级供给）。
    baseline = _trigger(
        client, admin_headers, run_id, request_key="p2-route-no-roots",
        delivery_version_no=1,
    )["rerun"]
    assert baseline["source_records"] == []

    # 服务级：同一 run、同一版本，只是把 host_id → 本地根交给服务。
    with factory() as db:
        outcome = compat_module.trigger_compat_rerun(
            db, plan_run_id=run_id, platform=PLATFORM,
            requested_by="owner", requester_role="admin",
            request_key="p2-service-supply", delivery_version_no=1,
            local_source_roots={HOST: str(agent_root)},
        )
        db.commit()
        row = outcome.rerun
        assert row.status == facts.COMPAT_RERUN_STATUS_COMPLETED
        assert row.is_partial is False
        assert sorted(rec["source_kind"] for rec in row.source_records) == [
            "agent_local_original", "agent_local_original",
        ]
        assert {rec["dle_id"] for rec in row.source_records} == {a_id, b_id}
        assert {rec["i4_status"] for rec in row.source_records} == {"i4_local_protected"}
        copied = {rec["dle_id"]: Path(rec["copied_path"]) for rec in row.source_records}
        assert all(path.is_dir() and (path / "main.dbg").is_file() for path in copied.values())
        assert all(
            str(path).startswith(str(root / "compat_reruns" / str(run_id) / PLATFORM))
            for path in copied.values()
        )
        # 副本保留 aee 采集布局，Path 可被下游按目录结构反解。
        xls_rows = _read_report(row.merge_xls_path)
        assert xls_rows[0]["Path"].endswith("/aee_exp/" + copied[a_id].name)


def test_p3_g6_path_only_covers_representatives(route_env):
    """P3：G6 认证上送/校验通路按身份只覆盖代表；非代表既无待办也不能回执。"""
    client, factory, root, admin_headers = route_env
    run_id, a_id, b_id, agent_root = _seed_agent_only(factory, root)

    with factory() as db:
        created = g6.ensure_partition_publications(db, plan_run_id=run_id, platform=PLATFORM)
        db.commit()
        assert created == 1
        tasks = g6.list_agent_tasks(db, host_id=HOST)
        assert {task["dle_id"] for task in tasks} == {a_id}
        assert b_id not in {task["dle_id"] for task in tasks}

        # 非代表即使被强行登记发布记录，真实回执服务仍按身份拒绝（不是路径检查的副产品）。
        forced = facts.record_evidence_publication(
            db, plan_run_id=run_id, platform=PLATFORM, dle_id=b_id,
            root_plan_run_id=run_id, source_kind=facts.PUBLICATION_SOURCE_LOCAL,
            source_path=None, collection_folder=COLLECTION_FOLDER,
            naming_rule_version=g6.NAMING_RULE_VERSION,
            destination_rel_path=evidence_rel_path(
                root_plan_run_id=run_id, collection_folder=COLLECTION_FOLDER,
                serial="SN-NONREP-LOCAL", event_dir=Path(
                    db.get(DeviceLogEvent, uuid.UUID(b_id)).local_path).name,
            ),
            executor_host_id=HOST,
        )
        db.flush()
        with pytest.raises(g6.EvidencePublicationError) as exc:
            g6.record_agent_receipt(
                db, publication_id=forced.id, host_id=HOST, dle_id=b_id,
                destination_rel_path=forced.destination_rel_path,
                state=facts.PUBLICATION_STATE_VERIFIED,
                checksum="0" * 64, size_bytes=1, attempts=1,
            )
        assert exc.value.code == "EVIDENCE_NOT_REPRESENTATIVE"
        db.rollback()

    # 代表走完整 G6 上送（Agent 侧同一条复制+哈希）后，重跑补跑只能救回代表。
    with factory() as db:
        task = g6.list_agent_tasks(db, host_id=HOST)[0]
        src = Path(task["local_path"])
        dest = root / task["destination_rel_path"]
        copytree_no_symlinks(src, dest, dest_root=root)
        checksum, size = dir_sha256_and_size(src)
        receipt = g6.record_agent_receipt(
            db, publication_id=int(task["publication_id"]), host_id=HOST,
            dle_id=task["dle_id"], destination_rel_path=task["destination_rel_path"],
            state=facts.PUBLICATION_STATE_VERIFIED, checksum=checksum,
            size_bytes=size, attempts=1,
        )
        db.commit()
        assert receipt["state"] == facts.PUBLICATION_STATE_VERIFIED

    after = _trigger(
        client, admin_headers, run_id, request_key="p3-after-g6",
        delivery_version_no=1,
    )["rerun"]
    assert {rec["source_kind"] for rec in after["source_records"]} == {"center_evidence_verified"}
    assert [item["dle_id"] for item in after["missing_items"]] == [b_id]
    assert after["is_partial"] is True


def test_p4_g6_copy_and_verify_core_is_family_agnostic(tmp_path):
    """P4：复制+哈希复核内核可用于 ``compat_reruns/`` 隔离目录；越根/symlink 仍拒绝。"""
    src = _event_dir(tmp_path, "agent-local", COLLECTION_FOLDER, "SN-1", "aee_exp", _event_name())
    events_dir = tmp_path / "compat_reruns" / "1216" / PLATFORM / "r1" / "events"
    events_dir.mkdir(parents=True)
    dest = events_dir / COLLECTION_FOLDER / "SN-1" / "aee_exp" / src.name

    copytree_no_symlinks(src, dest, dest_root=events_dir)
    src_checksum, src_size = dir_sha256_and_size(src)
    dest_checksum, dest_size = dir_sha256_and_size(dest)
    assert (src_checksum, src_size) == (dest_checksum, dest_size)

    # 越根目的（写入 evidence/ 主链或根外）一律拒绝。
    outside = tmp_path / "elsewhere" / "leaf"
    with pytest.raises(EvidenceLayoutError):
        copytree_no_symlinks(src, outside, dest_root=events_dir)

    # 原件里出现 symlink 时复制失败，且不留半成品目录。
    link_dir = _event_dir(tmp_path, "agent-local-bad", COLLECTION_FOLDER, "SN-2", "aee_exp", _event_name())
    os.symlink(str(src / "main.dbg"), str(link_dir / "link.dbg"))
    bad_dest = events_dir / "bad-leaf"
    with pytest.raises(EvidenceLayoutError):
        copytree_no_symlinks(link_dir, bad_dest, dest_root=events_dir)
    assert not bad_dest.exists()

    # 目的路径契约只认 evidence/ 前缀：供给副本族不能借用 G6 的 rel-path 校验。
    supply_rel = f"compat_reruns/1216/{PLATFORM}/r1/events/{COLLECTION_FOLDER}/SN-1/aee_exp/x"
    with pytest.raises(EvidenceLayoutError):
        parse_evidence_rel_path(supply_rel)
    with pytest.raises(ArtifactPathError):
        resolve_evidence_event_path(supply_rel, root_plan_run_id=1216, serial="SN-1")
