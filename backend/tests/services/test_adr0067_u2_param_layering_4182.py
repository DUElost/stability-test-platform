"""ADR-0067 U2 验收测试：五层合并序、#2649 统一判定集、stp-l1-v2 指纹、锁序与冻结、log_prepare@1.1.0 种子。

对照 ADR-0067 §5 全部 U2 边界反例（含真实隔离 PostgreSQL 并发交错取证）。
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import threading
from unittest.mock import patch

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from backend.models.enums import HostStatus, JobStatus
from backend.models.host import Device, Host
from backend.models.job import JobInstance
from backend.models.plan import Plan, PlanStep
from backend.models.plan_run import PlanRun
from backend.models.project import TestProject
from backend.models.project_model import ProjectModel
from backend.models.script import Script
from backend.services.plan_confirmation import (
    PlanConfirmationChanged,
    assert_confirmation_matches,
    canonical_confirmation_document,
    fingerprint_format_ok,
    load_plan_graph_for_confirmation,
)
from backend.services.plan_dispatcher_core import (
    PlanDispatchError,
    build_lifecycle_from_snapshot,
    inject_network_params,
    inject_suite_params,
    inject_wifi_params,
)
from backend.services.plan_dispatcher_sync import (
    _prepare_queued_plan_run,
    dispatch_plan_sync,
    materialize_serial_rejected_jobs,
    prepare_plan_run,
    preview_plan_dispatch_sync,
)
from backend.services.plan_parameter_projection import project_job, project_snapshot
from backend.services.project_log_profile import (
    STATUS_APPLIED,
    STATUS_PENDING_DEVICES,
    STATUS_SKIPPED_DEVICE_MISMATCH,
    STATUS_SKIPPED_EMPTY_PROFILE,
    STATUS_SKIPPED_PROJECT_INACTIVE,
    STATUS_SKIPPED_UNBOUND_PLAN,
    resolve_project_log_profile_for_dispatch,
    resolve_project_log_profile_static,
)
from backend.services.script_catalog import sync_scripts_from_manifest

REPO_ROOT = Path(__file__).resolve().parents[3]
MIGRATION_PATH = (
    REPO_ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "a67u2b3c4d5e6_seed_log_prepare_v110_defaults.py"
)


def _load_seed_migration():
    spec = importlib.util.spec_from_file_location("a67u2_seed_mod", MIGRATION_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _ensure_log_prepare_v110(db) -> Script:
    mod = _load_seed_migration()
    row = (
        db.query(Script)
        .filter_by(name="log_prepare", version="1.1.0")
        .one_or_none()
    )
    if row is None:
        row = Script(
            name="log_prepare",
            display_name="log_prepare",
            category="device",
            script_type="python",
            version="1.1.0",
            nfs_path="/opt/stability-test-agent/agent/scripts/log_prepare/v1.1.0/log_prepare.py",
            content_sha256=mod.SCRIPT_ENTRY_SHA256,
            param_schema=dict(mod.LOG_PREPARE_V110_PARAM_SCHEMA),
            default_params=dict(mod.LOG_PREPARE_V110_DEFAULT_PARAMS),
            is_active=True,
        )
        db.add(row)
        db.flush()
    return row


def _make_project(
    db,
    key: str,
    *,
    source: str = "USER",
    status: str = "ACTIVE",
    log_profile: dict | None = None,
    version: int = 0,
) -> TestProject:
    proj = TestProject(
        project_key=key,
        display_name=key,
        source=source,
        status=status,
        log_profile=log_profile,
        log_profile_version=version,
    )
    db.add(proj)
    db.flush()
    return proj


def _bind_model(db, project: TestProject, model: str, *, active: bool = True) -> ProjectModel:
    row = ProjectModel(
        project_id=project.id,
        match_value=model,
        is_active=active,
    )
    db.add(row)
    db.flush()
    return row


def _make_host_and_device(
    db,
    host_id: str,
    serial: str,
    *,
    model: str | None = None,
    status: str = "ONLINE",
) -> Device:
    host = db.get(Host, host_id)
    if host is None:
        host = Host(id=host_id, hostname=host_id, status=HostStatus.ONLINE.value)
        db.add(host)
        db.flush()
    dev = Device(serial=serial, host_id=host_id, model=model, status=status)
    db.add(dev)
    db.flush()
    return dev


def _make_log_plan(
    db,
    name: str,
    *,
    project_id: int | None = None,
    step_params: dict | None = None,
    script_version: str = "1.1.0",
    extra_steps: list[PlanStep] | None = None,
) -> Plan:
    plan = Plan(name=name, project_id=project_id, created_by="tester")
    db.add(plan)
    db.flush()
    db.add(
        PlanStep(
            plan_id=plan.id,
            step_key="log_prepare",
            script_name="log_prepare",
            script_version=script_version,
            stage="init",
            sort_order=0,
            params=step_params if step_params is not None else {},
            enabled=True,
        )
    )
    for step in extra_steps or []:
        step.plan_id = plan.id
        db.add(step)
    db.flush()
    return plan


class TestCounterexamples1To3ParamLayering:
    """反例 1–3：绑定项目一致生效、GPU 专项覆写优先、显式 null 取消项目预设。"""

    def test_applied_preset_freezes_top_level_and_never_mutates_step_params(self, db_session):
        _ensure_log_prepare_v110(db_session)
        proj = _make_project(
            db_session,
            "u2-honor-a",
            log_profile={"aee_mode": "3", "mobilelog_size_mb": 2048},
            version=1,
        )
        _bind_model(db_session, proj, "MLD_NX2")
        d1 = _make_host_and_device(db_session, "h-u2-1", "SN-U2-001", model="MLD_NX2")
        plan = _make_log_plan(db_session, "u2-monkey", project_id=proj.id, step_params={})
        db_session.commit()

        preview = preview_plan_dispatch_sync(plan.id, [d1.id], db_session)
        assert preview["project_log_profile"]["status"] == STATUS_APPLIED
        assert preview["project_log_profile"]["log_profile_version"] == 1
        assert preview["confirmation_fingerprint"].startswith("stp-l1-v2:")
        init_params = preview["lifecycle"]["init"][0]["params"]
        assert init_params["aee_mode"] == "3"
        assert init_params["mobilelog_size_mb"] == 2048
        assert init_params["ylog_size_mb"] == 4096

        # L1 preview projection annotates project_preset + project_key@v1
        l1_items = {
            tuple(item["path"]): item
            for item in preview["parameter_projection"]["steps"][0]["params"]
        }
        assert l1_items[("aee_mode",)]["source"] == "project_preset"
        assert l1_items[("aee_mode",)]["authority"] == "project_preset:u2-honor-a@v1"
        assert l1_items[("mobilelog_size_mb",)]["source"] == "project_preset"
        assert l1_items[("ylog_size_mb",)]["source"] == "script_default"

        pr = prepare_plan_run(
            plan.id,
            [d1.id],
            "tester",
            db_session,
            confirmation_fingerprint=preview["confirmation_fingerprint"],
        )
        # 顶层不可变冻结，绝不写回 steps[].params（D5.1）
        assert pr.plan_snapshot["project_log_profile"]["status"] == STATUS_APPLIED
        assert pr.plan_snapshot["project_log_profile"]["effective_profile"] == {
            "aee_mode": "3",
            "mobilelog_size_mb": 2048,
        }
        assert pr.plan_snapshot["steps"][0]["params"] == {}

        # L2 & L3 投影均识别 project_preset
        l2 = project_snapshot(
            pr.plan_snapshot, pr.run_context, plan_id=plan.id, plan_run_id=pr.id,
        )
        l2_items = {tuple(item.path): item for item in l2.steps[0].params}
        assert l2_items[("mobilelog_size_mb",)].source == "project_preset"
        assert l2_items[("mobilelog_size_mb",)].value == 2048
        assert l2_items[("mobilelog_size_mb",)].authority == "project_preset:u2-honor-a@v1"
        assert l2.project_log_profile is not None
        assert l2.project_log_profile.status == STATUS_APPLIED

        lc = build_lifecycle_from_snapshot(pr.plan_snapshot)
        l3 = project_job(
            plan_snapshot=pr.plan_snapshot,
            run_context=pr.run_context,
            job_id=99,
            device_id=d1.id,
            host_id="h-u2-1",
            plan_run_id=pr.id,
            plan_id=plan.id,
            pipeline_def={"lifecycle": lc},
            allocation_params=None,
            claim_watcher_policy=None,
        )
        l3_items = {tuple(item.path): item for item in l3.steps[0].params}
        assert l3_items[("aee_mode",)].source == "project_preset"
        assert l3_items[("aee_mode",)].authority == "project_preset:u2-honor-a@v1"
        assert l3_items[("mobilelog_size_mb",)].value == 2048

    def test_gpu_step_override_wins_while_non_overridden_keys_inherit_preset(self, db_session):
        _ensure_log_prepare_v110(db_session)
        proj = _make_project(
            db_session,
            "u2-honor-gpu",
            log_profile={"aee_mode": "3", "mobilelog_size_mb": 2048, "ylog_size_mb": 2048},
            version=2,
        )
        _bind_model(db_session, proj, "ELA_LX2")
        d1 = _make_host_and_device(db_session, "h-u2-gpu", "SN-U2-GPU1", model="ELA_LX2")
        plan = _make_log_plan(
            db_session,
            "u2-gpu-plan",
            project_id=proj.id,
            step_params={"mobilelog_size_mb": 10240, "ylog_size_mb": 10240},
        )
        db_session.commit()

        preview = preview_plan_dispatch_sync(plan.id, [d1.id], db_session)
        params = preview["lifecycle"]["init"][0]["params"]
        assert params["mobilelog_size_mb"] == 10240
        assert params["ylog_size_mb"] == 10240
        assert params["aee_mode"] == "3"
        l1_items = {
            tuple(item["path"]): item
            for item in preview["parameter_projection"]["steps"][0]["params"]
        }
        assert l1_items[("mobilelog_size_mb",)]["source"] == "step_override"
        assert l1_items[("ylog_size_mb",)]["source"] == "step_override"
        assert l1_items[("aee_mode",)]["source"] == "project_preset"

    def test_explicit_null_step_override_cancels_project_aee_mode(self, db_session):
        _ensure_log_prepare_v110(db_session)
        proj = _make_project(
            db_session,
            "u2-null-cancel",
            log_profile={"aee_mode": "3", "mtk_sublog_cmd": "set_sublog_4_5_0"},
            version=1,
        )
        _bind_model(db_session, proj, "MLD_NX3")
        d1 = _make_host_and_device(db_session, "h-u2-null", "SN-U2-NULL1", model="MLD_NX3")
        plan = _make_log_plan(
            db_session,
            "u2-cancel-aee",
            project_id=proj.id,
            step_params={"aee_mode": None},
        )
        db_session.commit()

        preview = preview_plan_dispatch_sync(plan.id, [d1.id], db_session)
        params = preview["lifecycle"]["init"][0]["params"]
        assert params["aee_mode"] is None
        assert params["mtk_sublog_cmd"] == "set_sublog_4_5_0"
        l1_items = {
            tuple(item["path"]): item
            for item in preview["parameter_projection"]["steps"][0]["params"]
        }
        assert l1_items[("aee_mode",)]["source"] == "step_override"
        assert l1_items[("aee_mode",)]["value"] is None


class TestCounterexample4SerialConflictPartition:
    """反例 4：#2649 正常设备 A + 冲突设备 B 同选可确认派发（无确认环死锁），全冲突统一拒绝。"""

    def test_normal_plus_serial_conflict_shares_evaluated_set_and_confirms(self, db_session):
        _ensure_log_prepare_v110(db_session)
        proj_a = _make_project(
            db_session, "u2-proj-a", log_profile={"aee_mode": "3"}, version=1,
        )
        proj_b = _make_project(
            db_session, "u2-proj-b", log_profile={"aee_mode": "4"}, version=1,
        )
        _bind_model(db_session, proj_a, "MODEL_A")
        _bind_model(db_session, proj_b, "MODEL_B")

        dev_a = _make_host_and_device(db_session, "h-sc-1", "REAL-SN-A001", model="MODEL_A")
        # 占位序列号设备（归属项目 B，若不剔除会误判为 skipped_device_mismatch）
        dev_b = _make_host_and_device(
            db_session, "h-sc-1", "0123456789ABCDEF", model="MODEL_B",
        )
        plan = _make_log_plan(db_session, "u2-sc-plan", project_id=proj_a.id)
        db_session.commit()

        requested = [dev_a.id, dev_b.id]
        preview = preview_plan_dispatch_sync(plan.id, requested, db_session)
        # preview["device_ids"] 必须保留原始请求设备集（保护 PlanExecutePage frozenDeviceIds）
        assert preview["device_ids"] == requested
        plp = preview["project_log_profile"]
        assert plp["status"] == STATUS_APPLIED
        assert plp["evaluated_device_ids"] == [dev_a.id]
        assert plp["excluded_serial_conflict_device_ids"] == [dev_b.id]

        # 同一选择与指纹调用 prepare_plan_run 必须成功，无确认环死锁
        pr = prepare_plan_run(
            plan.id,
            requested,
            "tester",
            db_session,
            confirmation_fingerprint=preview["confirmation_fingerprint"],
        )
        assert pr.plan_snapshot["project_log_profile"]["status"] == STATUS_APPLIED
        assert pr.plan_snapshot["project_log_profile"]["evaluated_device_ids"] == [dev_a.id]
        assert pr.plan_snapshot["project_log_profile"]["excluded_serial_conflict_device_ids"] == [dev_b.id]
        assert pr.run_context["dispatch_device_ids"] == [dev_a.id]
        assert len(pr.run_context["dispatch_rejected_devices"]) == 1
        assert pr.run_context["dispatch_rejected_devices"][0]["id"] == dev_b.id

        # 准入阶段为冲突设备 B 物化 FAILED job 时只读冻结快照
        created_failed = materialize_serial_rejected_jobs(db_session, pr)
        db_session.commit()
        assert created_failed == 1
        failed_job = (
            db_session.query(JobInstance)
            .filter_by(plan_run_id=pr.id, device_id=dev_b.id)
            .one()
        )
        assert failed_job.status == JobStatus.FAILED.value
        assert failed_job.pipeline_def["lifecycle"]["init"][0]["params"]["aee_mode"] == "3"

    def test_all_serial_conflict_rejected_by_both_preview_and_prepare(self, db_session):
        _ensure_log_prepare_v110(db_session)
        proj = _make_project(db_session, "u2-all-sc", log_profile={"aee_mode": "3"}, version=1)
        dev_sc = _make_host_and_device(db_session, "h-sc-all", "0123456789ABCDEF", model="M")
        plan = _make_log_plan(db_session, "u2-all-sc-plan", project_id=proj.id)
        db_session.commit()

        with pytest.raises(PlanDispatchError, match="serial conflicts"):
            preview_plan_dispatch_sync(plan.id, [dev_sc.id], db_session)
        with pytest.raises(PlanDispatchError, match="serial conflicts"):
            prepare_plan_run(plan.id, [dev_sc.id], "tester", db_session)


class TestCounterexample7And11FallbackAndScheduleChain:
    """反例 7 / 11：混跑、未映射、未绑定 Plan 不反推、归档/SEED 项目跳过、定时与链式一致。"""

    def test_mixed_and_unmapped_devices_fallback_without_blocking(self, db_session):
        _ensure_log_prepare_v110(db_session)
        proj_a = _make_project(
            db_session, "u2-mix-a", log_profile={"mobilelog_size_mb": 2048, "aee_mode": "3"}, version=1,
        )
        proj_b = _make_project(db_session, "u2-mix-b")
        _bind_model(db_session, proj_a, "MIX_A")
        _bind_model(db_session, proj_b, "MIX_B")
        da = _make_host_and_device(db_session, "h-mix", "SN-MIX-A", model="MIX_A")
        db_other = _make_host_and_device(db_session, "h-mix", "SN-MIX-B", model="MIX_B")
        d_unmapped = _make_host_and_device(db_session, "h-mix", "SN-MIX-U", model="UNMAPPED_MODEL")
        plan = _make_log_plan(db_session, "u2-mix-plan", project_id=proj_a.id)
        db_session.commit()

        preview = preview_plan_dispatch_sync(plan.id, [da.id, db_other.id, d_unmapped.id], db_session)
        plp = preview["project_log_profile"]
        assert plp["status"] == STATUS_SKIPPED_DEVICE_MISMATCH
        assert plp["effective_profile"] == {}
        assert plp["mismatch_summary"] == {
            "unmapped_count": 1,
            "other_project_keys": ["u2-mix-b"],
            "other_project_device_count": 1,
        }
        # 回退至脚本默认 4096，不应用 aee_mode=3
        init_params = preview["lifecycle"]["init"][0]["params"]
        assert init_params["mobilelog_size_mb"] == 4096
        assert "aee_mode" not in init_params

        # 不阻断派发（ADR-0029 D5 保持挂起）
        pr = prepare_plan_run(
            plan.id,
            [da.id, db_other.id, d_unmapped.id],
            "tester",
            db_session,
            confirmation_fingerprint=preview["confirmation_fingerprint"],
        )
        assert pr.status == "QUEUED"
        assert pr.plan_snapshot["project_log_profile"]["status"] == STATUS_SKIPPED_DEVICE_MISMATCH

    def test_unbound_plan_never_reverse_infers_log_profile(self, db_session):
        _ensure_log_prepare_v110(db_session)
        proj = _make_project(
            db_session, "u2-unbound-proj", log_profile={"aee_mode": "3"}, version=1,
        )
        _bind_model(db_session, proj, "UNBOUND_M")
        d1 = _make_host_and_device(db_session, "h-ub", "SN-UB-1", model="UNBOUND_M")
        plan = _make_log_plan(db_session, "u2-unbound-plan", project_id=None)
        db_session.commit()

        preview = preview_plan_dispatch_sync(plan.id, [d1.id], db_session)
        assert preview["project_log_profile"]["status"] == STATUS_SKIPPED_UNBOUND_PLAN
        assert "aee_mode" not in preview["lifecycle"]["init"][0]["params"]

        pr = prepare_plan_run(plan.id, [d1.id], "tester", db_session)
        # 报表维度 plan_run.project_id 仍由 _infer_frozen_project_id 推断，但不触发 log_profile
        assert pr.project_id == proj.id
        assert pr.plan_snapshot["project_log_profile"]["status"] == STATUS_SKIPPED_UNBOUND_PLAN

    def test_archived_seed_empty_not_applicable_and_schedule_chain_parity(self, db_session):
        _ensure_log_prepare_v110(db_session)
        archived = _make_project(
            db_session, "u2-arch", status="ARCHIVED", log_profile={"aee_mode": "3"}, version=1,
        )
        seed_proj = _make_project(
            db_session, "u2-seed", source="SEED", log_profile={"aee_mode": "3"}, version=1,
        )
        empty_proj = _make_project(db_session, "u2-empty", log_profile=None, version=0)
        active_proj = _make_project(
            db_session, "u2-active-sc", log_profile={"aee_mode": "3"}, version=1,
        )
        _bind_model(db_session, archived, "M_ARCH")
        _bind_model(db_session, seed_proj, "M_SEED")
        _bind_model(db_session, empty_proj, "M_EMPTY")
        _bind_model(db_session, active_proj, "M_ACTIVE_SC")
        d_arch = _make_host_and_device(db_session, "h-st", "SN-ARCH", model="M_ARCH")
        d_seed = _make_host_and_device(db_session, "h-st", "SN-SEED", model="M_SEED")
        d_empty = _make_host_and_device(db_session, "h-st", "SN-EMPTY", model="M_EMPTY")
        d_act = _make_host_and_device(db_session, "h-st", "SN-ACT", model="M_ACTIVE_SC")

        p_arch = _make_log_plan(db_session, "p-arch", project_id=archived.id)
        p_seed = _make_log_plan(db_session, "p-seed", project_id=seed_proj.id)
        p_empty = _make_log_plan(db_session, "p-empty", project_id=empty_proj.id)
        p_act = _make_log_plan(db_session, "p-act", project_id=active_proj.id)
        db_session.commit()

        assert preview_plan_dispatch_sync(p_arch.id, [d_arch.id], db_session)[
            "project_log_profile"
        ]["status"] == STATUS_SKIPPED_PROJECT_INACTIVE
        assert preview_plan_dispatch_sync(p_seed.id, [d_seed.id], db_session)[
            "project_log_profile"
        ]["status"] == STATUS_SKIPPED_PROJECT_INACTIVE
        assert preview_plan_dispatch_sync(p_empty.id, [d_empty.id], db_session)[
            "project_log_profile"
        ]["status"] == STATUS_SKIPPED_EMPTY_PROFILE

        # 定时调度与链式派发均经过 prepare_plan_run 并一致应用 D3
        sched = dispatch_plan_sync(p_act.id, [d_act.id], "cron", db_session, run_type="SCHEDULE")
        assert sched.plan_snapshot["project_log_profile"]["status"] == STATUS_APPLIED
        chained = prepare_plan_run(p_act.id, [d_act.id], "chain", db_session, run_type="CHAIN")
        assert chained.plan_snapshot["project_log_profile"]["status"] == STATUS_APPLIED


class TestCounterexample8FingerprintLocksAndConcurrency:
    """反例 8：v2 指纹防漂移、单次解析复用、PG FOR SHARE 锁序、冻结后解绑交错、快照隔离。"""

    def test_v2_fingerprint_rejects_v1_and_pending_devices_and_tracks_profile_changes(self, db_session):
        _ensure_log_prepare_v110(db_session)
        proj = _make_project(
            db_session, "u2-fp-proj", log_profile={"aee_mode": "3"}, version=1,
        )
        _bind_model(db_session, proj, "FP_M")
        d1 = _make_host_and_device(db_session, "h-fp", "SN-FP-1", model="FP_M")
        d_other = _make_host_and_device(db_session, "h-fp", "SN-FP-2", model="OTHER_M")
        plan = _make_log_plan(db_session, "u2-fp-plan", project_id=proj.id)
        db_session.commit()

        # pending_devices 绝不允许进入确认指纹（D4.1）
        plan_obj, steps = load_plan_graph_for_confirmation(db_session, plan.id)
        static_res = resolve_project_log_profile_static(db_session, steps, plan_project_id=proj.id)
        assert static_res["status"] == STATUS_PENDING_DEVICES
        with pytest.raises(ValueError, match="pending_devices"):
            canonical_confirmation_document(plan_obj, steps, {}, project_log_profile=static_res)

        # 旧 v1 指纹必须被拒绝，不降级（D4.4）
        v1_token = "stp-l1-v1:" + "a" * 64
        assert not fingerprint_format_ok(v1_token)
        with pytest.raises(PlanConfirmationChanged):
            assert_confirmation_matches(plan_obj, steps, {}, v1_token)

        # applied v1 → 改 log_profile 到 v2 → 旧指纹被拒
        preview_v1 = preview_plan_dispatch_sync(plan.id, [d1.id], db_session)
        token_v1 = preview_v1["confirmation_fingerprint"]
        proj.log_profile = {"aee_mode": "4"}
        proj.log_profile_version = 2
        db_session.commit()
        with pytest.raises(PlanConfirmationChanged):
            prepare_plan_run(plan.id, [d1.id], "tester", db_session, confirmation_fingerprint=token_v1)

        # non-applied（skipped_device_mismatch）下改 log_profile 版本不产生无关指纹失效（D4.1）
        preview_skip_1 = preview_plan_dispatch_sync(plan.id, [d1.id, d_other.id], db_session)
        assert preview_skip_1["project_log_profile"]["status"] == STATUS_SKIPPED_DEVICE_MISMATCH
        proj.log_profile = {"aee_mode": "5"}
        proj.log_profile_version = 3
        db_session.commit()
        preview_skip_2 = preview_plan_dispatch_sync(plan.id, [d1.id, d_other.id], db_session)
        assert preview_skip_2["confirmation_fingerprint"] == preview_skip_1["confirmation_fingerprint"]

        # applied → 归属解绑变 skipped_device_mismatch → 旧 applied 指纹被拒
        preview_applied = preview_plan_dispatch_sync(plan.id, [d1.id], db_session)
        db_session.query(ProjectModel).filter_by(project_id=proj.id, match_value="FP_M").delete()
        db_session.commit()
        with pytest.raises(PlanConfirmationChanged):
            prepare_plan_run(
                plan.id,
                [d1.id],
                "tester",
                db_session,
                confirmation_fingerprint=preview_applied["confirmation_fingerprint"],
            )

    def test_single_pass_resolution_reuse_in_preview_and_prepare(self, db_session):
        _ensure_log_prepare_v110(db_session)
        proj = _make_project(db_session, "u2-once", log_profile={"aee_mode": "3"}, version=1)
        _bind_model(db_session, proj, "ONCE_M")
        d1 = _make_host_and_device(db_session, "h-once", "SN-ONCE-1", model="ONCE_M")
        plan = _make_log_plan(db_session, "u2-once-plan", project_id=proj.id)
        db_session.commit()

        calls = {"n": 0}
        real_fn = resolve_project_log_profile_for_dispatch

        def counting(*args, **kwargs):
            calls["n"] += 1
            return real_fn(*args, **kwargs)

        with patch(
            "backend.services.plan_dispatcher_sync.resolve_project_log_profile_for_dispatch",
            counting,
        ):
            preview = preview_plan_dispatch_sync(plan.id, [d1.id], db_session)
            assert calls["n"] == 1
            calls["n"] = 0
            prepare_plan_run(
                plan.id,
                [d1.id],
                "tester",
                db_session,
                confirmation_fingerprint=preview["confirmation_fingerprint"],
            )
            assert calls["n"] == 1

    def test_pg_for_share_locks_project_and_post_resolution_unbind_freezes(
        self, db_session, engine,
    ):
        """真实 PG 并发交错：
        1. Plan (FOR SHARE) -> TestProject (FOR SHARE) 阻塞并发 TestProject FOR UPDATE；
        2. 单次 D3 读快照之后、snapshot 落库前并发解绑 project_model → 本次 Run 按冻结后语义完成 applied，
           紧随其后的新预览立即看到 skipped_device_mismatch；
        3. QUEUED 后修改 TestProject.log_profile 与删除 project_model 不影响已冻结快照重放与 L2/L3。
        """
        bind = db_session.get_bind()
        assert bind is not None and bind.dialect.name == "postgresql", (
            "U2 并发锁与读快照证据必须来自 PostgreSQL"
        )
        _ensure_log_prepare_v110(db_session)
        proj = _make_project(
            db_session, "u2-pg-lock", log_profile={"aee_mode": "3", "mobilelog_size_mb": 2048}, version=1,
        )
        _bind_model(db_session, proj, "PG_LOCK_M")
        d1 = _make_host_and_device(db_session, "h-pg-lock", "SN-PG-LOCK-1", model="PG_LOCK_M")
        plan = _make_log_plan(db_session, "u2-pg-lock-plan", project_id=proj.id)
        db_session.commit()

        preview = preview_plan_dispatch_sync(plan.id, [d1.id], db_session)
        token = preview["confirmation_fingerprint"]
        db_session.commit()

        plan_id = plan.id
        proj_id = proj.id
        dev_id = d1.id
        Session = sessionmaker(bind=engine, expire_on_commit=False)
        parked_evt = threading.Event()
        release_evt = threading.Event()
        errors: list[BaseException] = []
        result: dict = {}

        def parked(**kwargs):
            parked_evt.set()
            assert release_evt.wait(timeout=15), "主线程未释放快照写入"
            return _prepare_queued_plan_run(**kwargs)

        def worker():
            try:
                with patch(
                    "backend.services.plan_dispatcher_sync._prepare_queued_plan_run",
                    parked,
                ):
                    with Session() as sess:
                        pr = prepare_plan_run(
                            plan_id,
                            [dev_id],
                            "tester",
                            sess,
                            confirmation_fingerprint=token,
                        )
                        result["run_id"] = pr.id
                        result["snapshot_profile"] = pr.plan_snapshot["project_log_profile"]
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        t = threading.Thread(target=worker)
        t.start()
        assert parked_evt.wait(timeout=15), errors
        try:
            # 1) TestProject 行正被 FOR SHARE 锁定：并发 FOR UPDATE 必须超时阻塞
            with Session() as blocker:
                blocker.execute(text("SET LOCAL lock_timeout = '300ms'"))
                with pytest.raises(OperationalError):
                    blocker.execute(
                        text("SELECT id FROM test_project WHERE id = :pid FOR UPDATE"),
                        {"pid": proj_id},
                    )
            # 2) 单次 D3 读快照之后并发解绑 project_model（无跨表排他锁，不阻塞）
            with Session() as unbinder:
                unbinder.query(ProjectModel).filter_by(
                    project_id=proj_id, match_value="PG_LOCK_M",
                ).delete()
                unbinder.commit()
        finally:
            release_evt.set()
            t.join(timeout=20)

        assert not t.is_alive()
        assert errors == [], errors
        # 冻结后变更不影响在途本次 PlanRun（保持 applied v1）
        assert result["snapshot_profile"]["status"] == STATUS_APPLIED
        assert result["snapshot_profile"]["log_profile_version"] == 1

        # 紧随其后的新预览立即看到 skipped_device_mismatch
        with Session() as after_sess:
            next_preview = preview_plan_dispatch_sync(plan_id, [dev_id], after_sess)
            assert next_preview["project_log_profile"]["status"] == STATUS_SKIPPED_DEVICE_MISMATCH

            # 3) QUEUED 后改活表 log_profile，已冻结 PlanRun 重放与 L2 投影仍用 v1
            live_proj = after_sess.get(TestProject, proj_id)
            assert live_proj is not None
            live_proj.log_profile = {"aee_mode": "9", "mobilelog_size_mb": 8192}
            live_proj.log_profile_version = 2
            after_sess.commit()

            pr = after_sess.get(PlanRun, result["run_id"])
            assert pr is not None
            lc = build_lifecycle_from_snapshot(pr.plan_snapshot)
            assert lc["init"][0]["params"]["aee_mode"] == "3"
            assert lc["init"][0]["params"]["mobilelog_size_mb"] == 2048
            l2 = project_snapshot(
                pr.plan_snapshot, pr.run_context, plan_id=plan_id, plan_run_id=pr.id,
            )
            l2_items = {tuple(item.path): item for item in l2.steps[0].params}
            assert l2_items[("aee_mode",)].value == "3"
            assert l2_items[("aee_mode",)].source == "project_preset"
            assert l2_items[("aee_mode",)].authority == "project_preset:u2-pg-lock@v1"

    def test_historical_snapshot_without_project_log_profile_stays_compatible(self):
        historical = {
            "plan": {"id": 1, "name": "legacy", "watcher_policy": {}},
            "steps": [
                {
                    "stage": "init",
                    "step_key": "lp",
                    "script_name": "log_prepare",
                    "script_version": "1.0.0",
                    "param_schema": {},
                    "default_params": {},
                    "params": {"mobilelog_size_mb": 4096},
                    "enabled": True,
                    "sort_order": 0,
                }
            ],
        }
        lc = build_lifecycle_from_snapshot(historical)
        assert lc["init"][0]["params"] == {"mobilelog_size_mb": 4096}
        l2 = project_snapshot(historical, {}, plan_id=1, plan_run_id=1)
        assert l2.project_log_profile is None
        assert l2.steps[0].params[0].source == "step_override"
        assert l2.steps[0].params[0].value == 4096


class TestCounterexample5SeedMigrationAndScan:
    """反例 5：log_prepare@1.1.0 种子迁移幂等、#942 引用保护、1.0.0 不动、publish+scan 保留默认。"""

    def test_seed_migration_idempotent_reference_guard_and_scan_preserves_defaults(
        self, db_session, tmp_path,
    ):
        mod = _load_seed_migration()
        # 1) 验证种子 schema 边界与共享夹具完全一致
        assert mod.LOG_PREPARE_V110_PARAM_SCHEMA["mobilelog_size_mb"]["minimum"] == 256
        assert mod.LOG_PREPARE_V110_PARAM_SCHEMA["mobilelog_size_mb"]["maximum"] == 20480
        assert mod.LOG_PREPARE_V110_PARAM_SCHEMA["ylog_size_mb"]["minimum"] == 64
        assert mod.LOG_PREPARE_V110_PARAM_SCHEMA["ylog_size_mb"]["maximum"] == 10240
        assert mod.LOG_PREPARE_V110_DEFAULT_PARAMS == {
            "mobilelog_size_mb": 4096,
            "mtk_auto_start": True,
            "ylog_size_mb": 4096,
            "ylog_rotate": True,
            "ylog_required_streams": ["android", "kernel"],
        }

        # 2) 验证 #942 引用保护：若已有 1.1.0 空默认行且被 plan_step 引用，upgrade 必须抛 RuntimeError
        db_session.query(PlanStep).filter_by(script_name="log_prepare", script_version="1.1.0").delete()
        db_session.query(Script).filter_by(name="log_prepare", version="1.1.0").delete()
        db_session.commit()

        conn = db_session.connection()
        with patch.object(mod.op, "get_bind", return_value=conn):
            mod.upgrade()
            # 幂等重跑无副作用
            mod.upgrade()
        db_session.commit()

        row = db_session.query(Script).filter_by(name="log_prepare", version="1.1.0").one()
        assert row.default_params == mod.LOG_PREPARE_V110_DEFAULT_PARAMS
        assert row.param_schema == mod.LOG_PREPARE_V110_PARAM_SCHEMA
        assert row.package_sha256 is None

        # 3) --publish + scan 补齐 capabilities 与 package_sha256，绝不覆写 param_schema/default_params
        packer_spec = importlib.util.spec_from_file_location(
            "check_script_packages_mod",
            REPO_ROOT / "tools" / "dev" / "check_script_packages.py",
        )
        assert packer_spec and packer_spec.loader
        csp = importlib.util.module_from_spec(packer_spec)
        packer_spec.loader.exec_module(csp)
        packer = csp._load_packer()
        manifest_path = REPO_ROOT / "tool_manifest.json"
        doc = json.loads(manifest_path.read_text(encoding="utf-8"))
        # 只构造含 log_prepare 1.1.0 的最小 manifest & 包目录做确定性验证
        lp_entries = [
            e for e in doc["tools"]["log_prepare"]["versions"] if e["version"] == "1.1.0"
        ]
        assert len(lp_entries) == 1
        mini_manifest = tmp_path / "manifest.json"
        mini_manifest.write_text(
            json.dumps({"schema_version": 1, "tools": {"log_prepare": {"kind": "script", "versions": lp_entries}}}),
            encoding="utf-8",
        )
        pkg_root = tmp_path / "packages"
        tar_dest = pkg_root / "log_prepare" / "1.1.0.tar.gz"
        tar_dest.parent.mkdir(parents=True)
        facts = csp.build_family(
            REPO_ROOT / "backend" / "agent" / "scripts" / "log_prepare", packer, tar_dest,
        )
        assert facts is not None
        assert facts["package_sha256"] == lp_entries[0]["package_sha256"]

        scan_res = sync_scripts_from_manifest(db_session, mini_manifest, pkg_root)
        assert scan_res.conflicts == []
        assert scan_res.package_conflicts == []
        assert scan_res.package_missing == []
        assert scan_res.package_backfilled == 1

        db_session.refresh(row)
        assert row.package_sha256 == lp_entries[0]["package_sha256"]
        assert row.capabilities == ["progress_stamps"]
        assert row.default_params == mod.LOG_PREPARE_V110_DEFAULT_PARAMS
        assert row.param_schema == mod.LOG_PREPARE_V110_PARAM_SCHEMA

        # 4) 验证被 plan_step 引用时，试图修改不同默认或 downgrade 触发 #942 RuntimeError
        plan = _make_log_plan(db_session, "u2-ref-guard-plan")
        db_session.commit()
        assert plan.id is not None
        row.default_params = {}
        db_session.commit()
        conn2 = db_session.connection()
        with patch.object(mod.op, "get_bind", return_value=conn2):
            with pytest.raises(RuntimeError, match="seed migration aborted"):
                mod.upgrade()
            with pytest.raises(RuntimeError, match="seed migration aborted"):
                mod.downgrade()


class TestCounterexample10Layer5Compatibility:
    """反例 10：第 5 层填空（WiFi / 网络供给 / 套件）兼容、false 不视为空、#975 硬拒优先、与第 3 层正交。"""

    def test_wifi_and_network_fill_empty_keep_explicit_and_false(self):
        pipe = {
            "lifecycle": {
                "init": [
                    {
                        "step_id": "lp",
                        "action": "script:log_prepare",
                        "version": "1.1.0",
                        "params": {"mobilelog_size_mb": 2048, "aee_mode": "3"},
                    },
                    {
                        "step_id": "cw_empty",
                        "action": "script:connect_wifi",
                        "version": "1.0.0",
                        "params": {"ssid": "fixed-ssid", "password": ""},
                    },
                    {
                        "step_id": "cw_explicit",
                        "action": "script:connect_wifi",
                        "version": "1.0.0",
                        "params": {"ssid": "fixed-ssid", "password": "fixed-password"},
                    },
                    {
                        "step_id": "ms_empty",
                        "action": "script:monkey_setup",
                        "version": "2.0.0",
                        "params": {"wifi": {"ssid": "", "password": None}},
                    },
                ],
                "teardown": [],
            }
        }
        inject_network_params(
            pipe,
            {"resource_type": "wifi", "ssid": "pool-ssid", "password": "pool-password", "egress": "direct"},
        )
        steps = {s["step_id"]: s["params"] for s in pipe["lifecycle"]["init"]}
        # 第 3 层 log_prepare 与第 5 层零耦合
        assert steps["lp"] == {"mobilelog_size_mb": 2048, "aee_mode": "3"}
        # 空串密码被填空，非空 ssid/password 保持原值
        assert steps["cw_empty"] == {"ssid": "fixed-ssid", "password": "pool-password"}
        assert steps["cw_explicit"] == {"ssid": "fixed-ssid", "password": "fixed-password"}
        assert steps["ms_empty"]["wifi"] == {"ssid": "pool-ssid", "password": "pool-password"}

        # 布尔 false 绝不被视为可覆盖的空值（D2.5）
        bool_pipe = {
            "lifecycle": {
                "init": [
                    {
                        "step_id": "cw_bool",
                        "action": "script:connect_wifi",
                        "version": "1.0.0",
                        "params": {"ssid": False, "password": False},
                    }
                ],
                "teardown": [],
            }
        }
        inject_wifi_params(bool_pipe, {"ssid": "pool-ssid", "password": "pool-pw"})
        assert bool_pipe["lifecycle"]["init"][0]["params"] == {"ssid": False, "password": False}

    def test_suite_fill_zero_count_keep_false_and_reject_975_conflict_first(self):
        pipe = {
            "lifecycle": {
                "init": [
                    {
                        "step_id": "m1",
                        "action": "script:mtbf_run",
                        "version": "1.0.0",
                        "params": {"expected_testpoint_count": 0, "project": ""},
                    },
                    {
                        "step_id": "m2",
                        "action": "script:mtbf_check",
                        "version": "1.0.0",
                        "params": {"expected_testpoint_count": 42, "project": "honor_p1"},
                    },
                ],
                "teardown": [],
            }
        }
        inject_suite_params(pipe, {"expected_testpoint_count": 120, "project": "honor_p1"})
        m1, m2 = pipe["lifecycle"]["init"]
        assert m1["params"] == {"expected_testpoint_count": 120, "project": "honor_p1"}
        assert m2["params"] == {"expected_testpoint_count": 42, "project": "honor_p1"}

        # 布尔 false 不视为空值
        bool_pipe = {
            "lifecycle": {
                "init": [
                    {
                        "step_id": "m_bool",
                        "action": "script:mtbf_run",
                        "version": "1.0.0",
                        "params": {"expected_testpoint_count": False},
                    }
                ],
                "teardown": [],
            }
        }
        inject_suite_params(bool_pipe, {"expected_testpoint_count": 10})
        assert bool_pipe["lifecycle"]["init"][0]["params"]["expected_testpoint_count"] is False

        # #975 冲突硬拒先于填空
        conflict_pipe = {
            "lifecycle": {
                "init": [
                    {
                        "step_id": "m_conflict",
                        "action": "script:mtbf_run",
                        "version": "1.0.0",
                        "params": {"project": "wrong_dir", "expected_testpoint_count": 0},
                    }
                ],
                "teardown": [],
            }
        }
        with pytest.raises(PlanDispatchError, match="conflicts with suite export_dir"):
            inject_suite_params(
                conflict_pipe, {"expected_testpoint_count": 100, "project": "honor_p1"},
            )
