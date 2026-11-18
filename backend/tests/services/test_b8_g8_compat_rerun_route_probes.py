"""B8 G8 第 3 轮复核返修的路由级探针（#4034 评论 6948566219；#4031 返修判据 2/3）。

同一组探针在反例基线 ``03adb0bc2`` 上 RED、在返修 HEAD 上 GREEN（探针源码
两端逐字节相同）：

- B2 组合缺口（判据 3）：A 已复制但原生 org 缺失（Detail/ExpTime 等无法
  复原）、B 缺源 → 台账与 manifest 必须登记 FAILED，缺源清单仍列 B；仅缺源
  且已复制行字段齐全 → 保留部分成果（COMPLETED + partial）；只改变 B 的证据
  可用性后，同一字段缺口在全源时仍 FAILED。
- B3 并发交错（判据 2）：A 以 v1 复制未提交、B 以 v2 预读无登记；A 提交后
  B 锁内读到 COMPLETED/v1 → 必须先校验显式版本再决定重放，返回 409、实际
  复制为零、v1 登记原样；同 key 同版本与省略版本的合法重放继续通过。

全部走真实 HTTP 路由；每个请求经独立 PG16 会话（对齐生产 ``get_db``：不自动
提交），台账另用独立观测会话核对；文件操作在临时根。
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest
from sqlalchemy import text

from backend.core.database import get_db
from backend.main import fastapi_app as app
from backend.models.device_log_event import DeviceLogEvent
from backend.services import issue_fact_store as facts
from backend.tests.services.test_b8_g8_compat_rerun import (
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
    """真实路由 + 每请求独立 PG 会话（覆盖 client 的共享会话注入）。"""
    factory, _root = compat_env

    def _fresh_session_db():
        db = factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _fresh_session_db
    yield client
    app.dependency_overrides.pop(get_db, None)


def _seed_pair(
    factory, root, *, serial_a, serial_b, drop_a_org, with_second_version=False,
):
    """两个发生：A 可复制（轮次 1，org 可 drop）、B 缺源（轮次 2，org 可读）。

    ``with_second_version=True`` 时另登记 v2（范围含两个发生），供 B3 交错探针。
    返回 (run_id, dle_a_id, dle_b_id)。
    """
    with factory() as db:
        run = _run(db)
        name_a = _event_name()
        dle_a, sig_a, _occ_a = _dle(
            db, run, serial=serial_a, event=name_a, state="ARCHIVED",
            remote_path=str(_event_dir(root, "devices", str(run.id), name_a)),
        )
        dle_b, sig_b, _occ_b = _dle(
            db, run, serial=serial_b, event=_event_name(), state="PRUNED",
        )
        round_one = _seed_native_round(
            db, root, run, [dle_a], round_no=1, drop_org=drop_a_org,
        )
        round_two = _seed_native_round(db, root, run, [dle_b], round_no=2)
        facts.record_delivery_version(
            db, plan_run_id=run.id, platform=PLATFORM, version_no=1,
            software_versions=["V1"], round_ids=[round_one, round_two],
            basis_rounds=[
                {"round_id": round_one, "merge_generation": 1},
                {"round_id": round_two, "merge_generation": 1},
            ],
            effective_coverage=[], issue_ids=[sig_a.id, sig_b.id],
            event_ids=[str(dle_a.id), str(dle_b.id)],
            stats_snapshot_version="a" * 64,
        )
        if with_second_version:
            _version(
                db, run, no=2, issue_ids=[sig_a.id, sig_b.id],
                event_ids=[dle_a.id, dle_b.id], round_id=round_two,
            )
        db.commit()
        return run.id, dle_a.id, dle_b.id


def test_r3_b2_copied_row_field_gap_with_missing_source_fails(
    route_env, compat_env, admin_headers,
):
    """B2 组合：A 已复制但原生字段缺失、B 缺源 → 台账与 manifest 均 FAILED。

    缺源清单只列 B（is_partial 反映源覆盖），field_gaps 定位 A 的发生与字段；
    只改变 B 的证据可用性后，同一字段缺口在全源时仍 FAILED。
    """
    client = route_env
    factory, root = compat_env
    run_id, copied_id, missing_id = _seed_pair(
        factory, root, serial_a="SN-RA", serial_b="SN-RB", drop_a_org=True,
    )

    resp = client.post(
        ROUTE.format(run_id=run_id),
        params={
            "platform": PLATFORM, "request_key": "r3-combo",
            "delivery_version_no": 1,
        },
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text
    rerun = resp.json()["data"]["rerun"]
    assert rerun["status"] == facts.COMPAT_RERUN_STATUS_FAILED
    assert "native values unreconstructable" in (rerun["error"] or "")
    assert rerun["completed_at"] is None
    # 源覆盖轴：B 缺源 → partial + 缺源清单只列 B。
    assert rerun["is_partial"] is True
    assert [item["dle_id"] for item in rerun["missing_items"]] == [str(missing_id)]
    # 字段轴：缺口只定位 A（已复制行）。
    gaps = rerun["coverage_scope"]["field_gaps"]
    assert [gap["dle_id"] for gap in gaps] == [str(copied_id)]
    assert "Detail" in gaps[0]["fields"]
    assert "ExpTime" in gaps[0]["fields"]

    # 独立 PG16 观测会话核对台账终态。
    with factory() as db:
        row = facts.get_compat_rerun_by_request(
            db, plan_run_id=run_id, platform=PLATFORM, request_key="r3-combo",
        )
        assert row.status == facts.COMPAT_RERUN_STATUS_FAILED
        assert row.is_partial is True
        assert row.completed_at is None
        assert [item["dle_id"] for item in row.missing_items] == [str(missing_id)]
        assert [gap["dle_id"] for gap in row.coverage_scope["field_gaps"]] == [
            str(copied_id)
        ]

    manifest = json.loads(Path(rerun["manifest_path"]).read_text("utf-8"))
    assert manifest["registered_status"] == facts.COMPAT_RERUN_STATUS_FAILED
    assert manifest["is_partial"] is True
    assert [gap["dle_id"] for gap in manifest["field_gaps"]] == [str(copied_id)]

    rows = _read_report(rerun["merge_xls_path"])
    by_caused = {row["CausedBy"]: row for row in rows}
    # A 原生行缺失：CausedBy 来自已注册签名事实（非 org 原生值）。
    assert "/compat_reruns/" in by_caused["caused-SN-RA"]["Path"]
    assert by_caused["caused-SN-RA"]["ExpTime"] == ""
    assert by_caused["caused-SN-RA"]["Detail"] == ""
    assert by_caused["org-caused-SN-RB"]["Path"] == ""
    assert by_caused["org-caused-SN-RB"]["ExpTime"] == "Sun Nov 16 10:40:46 CST 2026"

    # 只改变 B 的证据可用性 → 全源时同一字段缺口仍 FAILED。
    with factory() as db:
        dle = db.get(DeviceLogEvent, missing_id)
        dle.state = "ARCHIVED"
        dle.remote_path = str(
            _event_dir(root, "devices", str(run_id), "evt-rb-late")
        )
        db.commit()
    full = client.post(
        ROUTE.format(run_id=run_id),
        params={
            "platform": PLATFORM, "request_key": "r3-combo-full",
            "delivery_version_no": 1,
        },
        headers=admin_headers,
    )
    assert full.status_code == 200, full.text
    rerun_full = full.json()["data"]["rerun"]
    assert rerun_full["status"] == facts.COMPAT_RERUN_STATUS_FAILED
    assert rerun_full["is_partial"] is False
    assert rerun_full["missing_items"] == []
    gaps_full = rerun_full["coverage_scope"]["field_gaps"]
    assert [gap["dle_id"] for gap in gaps_full] == [str(copied_id)]


def test_r3_b2_missing_source_only_keeps_partial(
    route_env, compat_env, admin_headers,
):
    """B2 对照：仅缺源、已复制行字段齐全 → 保留部分成果（COMPLETED + partial）。"""
    client = route_env
    factory, root = compat_env
    run_id, copied_id, missing_id = _seed_pair(
        factory, root, serial_a="SN-PC", serial_b="SN-PD", drop_a_org=False,
    )

    resp = client.post(
        ROUTE.format(run_id=run_id),
        params={
            "platform": PLATFORM, "request_key": "r3-partial",
            "delivery_version_no": 1,
        },
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text
    rerun = resp.json()["data"]["rerun"]
    assert rerun["status"] == facts.COMPAT_RERUN_STATUS_COMPLETED
    assert rerun["error"] is None
    assert rerun["completed_at"] is not None
    assert rerun["is_partial"] is True
    assert [item["dle_id"] for item in rerun["missing_items"]] == [str(missing_id)]
    assert rerun["coverage_scope"]["field_gaps"] == []

    with factory() as db:
        row = facts.get_compat_rerun_by_request(
            db, plan_run_id=run_id, platform=PLATFORM, request_key="r3-partial",
        )
        assert row.status == facts.COMPAT_RERUN_STATUS_COMPLETED
        assert row.is_partial is True
        assert row.completed_at is not None
        assert row.coverage_scope["field_gaps"] == []

    manifest = json.loads(Path(rerun["manifest_path"]).read_text("utf-8"))
    assert manifest["registered_status"] == facts.COMPAT_RERUN_STATUS_COMPLETED
    assert manifest["field_gaps"] == []

    rows = _read_report(rerun["merge_xls_path"])
    by_caused = {row["CausedBy"]: row for row in rows}
    # 已复制行字段齐全：原生列值逐字保留。
    assert "/compat_reruns/" in by_caused["org-caused-SN-PC"]["Path"]
    assert by_caused["org-caused-SN-PC"]["ExpTime"] == "Sun Nov 16 10:40:46 CST 2026"
    assert by_caused["org-caused-SN-PC"]["Detail"].startswith("Device_id: SN-PC")
    # 缺源行按原生行写字段，Path 留空。
    assert by_caused["org-caused-SN-PD"]["Path"] == ""
    assert by_caused["org-caused-SN-PD"]["ExpTime"] == "Sun Nov 16 10:40:46 CST 2026"


def test_r3_b3_cross_version_in_lock_rejected_with_zero_copy(
    route_env, compat_env, admin_headers,
):
    """B3 并发交错：A 以 v1 复制未提交、B 以 v2 预读无登记；A 提交后 B 锁内
    读到 COMPLETED/v1 → 409（不重放、零复制、v1 原样）；合法重放继续通过。
    """
    from backend.services import compat_rerun as compat_module

    client = route_env
    factory, root = compat_env
    run_id, first_id, _second_id = _seed_pair(
        factory, root, serial_a="SN-RG", serial_b="SN-RH", drop_a_org=False,
        with_second_version=True,
    )

    a_in_copy = threading.Event()
    b_resolved = threading.Event()
    a_committed = threading.Event()
    copy_calls = {"n": 0}
    call_lock = threading.Lock()
    lock_pids = {}
    original_copy = compat_module.copytree_no_symlinks
    original_get_dv = facts.get_delivery_version
    original_lock = facts.get_compat_rerun_for_update

    def gated_copy(src, dst, **kwargs):
        # A 停在复制中（主事务未提交），等 B 完成预读后再继续。
        a_in_copy.set()
        assert b_resolved.wait(timeout=15)
        with call_lock:
            copy_calls["n"] += 1
        return original_copy(src, dst, **kwargs)

    def gated_get_dv(db, **kwargs):
        row = original_get_dv(db, **kwargs)
        if int(kwargs.get("version_no") or 0) == 2:
            # B 的预读已发生（A 尚未提交），停在锁前等 A 提交。
            b_resolved.set()
            assert a_committed.wait(timeout=15)
        return row

    def observing_lock(db, **kwargs):
        row = original_lock(db, **kwargs)
        try:
            pid = db.execute(text("SELECT pg_backend_pid()")).scalar()
        except Exception:  # noqa: BLE001 — 非 PG 后端时跳过
            pid = None
        lock_pids["b" if row is not None else "a"] = pid
        return row

    results: dict[str, dict] = {}

    def attempt(name, version_no):
        resp = client.post(
            ROUTE.format(run_id=run_id),
            params={
                "platform": PLATFORM, "request_key": "r3-crossv",
                "delivery_version_no": version_no,
            },
            headers=admin_headers,
        )
        payload = {"status_code": resp.status_code}
        try:
            payload["body"] = resp.json()
        except ValueError:
            payload["body"] = None
        results[name] = payload
        if name == "a":
            a_committed.set()

    compat_module.copytree_no_symlinks = gated_copy
    facts.get_delivery_version = gated_get_dv
    facts.get_compat_rerun_for_update = observing_lock
    threads = []
    try:
        thread_a = threading.Thread(target=attempt, args=("a", 1))
        threads.append(thread_a)
        thread_a.start()
        assert a_in_copy.wait(timeout=15)
        thread_b = threading.Thread(target=attempt, args=("b", 2))
        threads.append(thread_b)
        thread_b.start()
        thread_a.join(timeout=30)
        thread_b.join(timeout=30)
    finally:
        compat_module.copytree_no_symlinks = original_copy
        facts.get_delivery_version = original_get_dv
        facts.get_compat_rerun_for_update = original_lock
    assert not any(thread.is_alive() for thread in threads), results

    # A 以 v1 完成；B 显式 v2 在锁内被拒（409），未发生重放或复制。
    body_a = results["a"]["body"]["data"]
    assert results["a"]["status_code"] == 200, results
    assert body_a["replayed"] is False, results
    assert body_a["rerun"]["status"] == facts.COMPAT_RERUN_STATUS_COMPLETED, results
    assert body_a["rerun"]["attempts"] == 1, results
    assert body_a["rerun"]["source_delivery_version_no"] == 1, results
    assert results["b"]["status_code"] == 409, results
    assert "bound to delivery version v1" in results["b"]["body"]["detail"], results
    assert copy_calls["n"] == 1, results
    if lock_pids.get("a") is not None and lock_pids.get("b") is not None:
        # 两个并发请求落在不同 PG16 后端会话。
        assert lock_pids["a"] != lock_pids["b"], lock_pids

    # v1 登记原样：状态、尝试次数、版本、产物与完成时间不被 409 触碰。
    with factory() as db:
        row = facts.get_compat_rerun_by_request(
            db, plan_run_id=run_id, platform=PLATFORM, request_key="r3-crossv",
        )
        assert row.status == facts.COMPAT_RERUN_STATUS_COMPLETED
        assert row.attempts == 1
        assert row.source_delivery_version_no == 1
        assert row.error is None
        assert row.completed_at is not None
        manifest = json.loads(Path(row.manifest_path).read_text("utf-8"))
        assert manifest["registered_status"] == facts.COMPAT_RERUN_STATUS_COMPLETED
        assert manifest["source_delivery_version_no"] == 1
        assert manifest["coverage_scope"]["covered_dle_ids"] == [str(first_id)]
        stale_completed_at = row.completed_at
        stale_manifest_path = row.manifest_path
        stale_xls_path = row.merge_xls_path

    # 同 key 同版本的合法重放继续通过（返回 v1 登记，不新增尝试）。
    replay_same = client.post(
        ROUTE.format(run_id=run_id),
        params={
            "platform": PLATFORM, "request_key": "r3-crossv",
            "delivery_version_no": 1,
        },
        headers=admin_headers,
    )
    assert replay_same.status_code == 200, replay_same.text
    assert replay_same.json()["data"]["replayed"] is True
    assert replay_same.json()["data"]["rerun"]["attempts"] == 1

    # 省略版本的合法重放沿用绑定版本。
    replay_omit = client.post(
        ROUTE.format(run_id=run_id),
        params={"platform": PLATFORM, "request_key": "r3-crossv"},
        headers=admin_headers,
    )
    assert replay_omit.status_code == 200, replay_omit.text
    assert replay_omit.json()["data"]["replayed"] is True
    assert replay_omit.json()["data"]["rerun"]["attempts"] == 1
    assert replay_omit.json()["data"]["rerun"]["source_delivery_version_no"] == 1

    with factory() as db:
        assert (
            db.query(facts.CompatRerun)
            .filter_by(plan_run_id=run_id, request_key="r3-crossv")
            .count()
            == 1
        )
        row = facts.get_compat_rerun_by_request(
            db, plan_run_id=run_id, platform=PLATFORM, request_key="r3-crossv",
        )
        assert row.completed_at == stale_completed_at
        assert row.manifest_path == stale_manifest_path
        assert row.merge_xls_path == stale_xls_path
