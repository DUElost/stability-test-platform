# -*- coding: utf-8 -*-
"""ADR-0030 v1.4 — Plan↔Suite 绑定：冻结 / 注入参数 / precheck 五步门禁。

绑定机制 = ``plan.suite_id`` 可空外键（NULL = P0 文件真源模式，不加门禁；
非空 = 托管模式全门禁）。本模块是绑定语义的**控制面唯一实现**，三组职责：

1. **prepare 冻结**（P1 设计 §3.2）：``freeze_dispatch_suite`` 产出
   ``run_context.dispatch_suite``——准入时刻的基线指纹，供事后归因（D5）。
2. **步骤参数注入**（P1 设计 §3.4）：``step_params_for_dispatch`` +
   ``plan_dispatcher_core.inject_suite_params`` 对 ``mtbf_`` 步骤注入
   ``{expected_testpoint_count, project}``，无需用户声明 default_params。
3. **precheck 门禁**（P1 设计 §3.3 + #965 / #975）：``collect_suite_gate_error``
   按活表套件行 + 磁盘文件逐项校验；套件**身份**以 Run 冻结
   ``dispatch_suite.suite_id`` 为准（#965），无冻结时回落 ``plan.suite_id``；
   并拒绝与 ``export_dir`` 冲突的 mtbf ``project`` 覆盖（#975）；任一失败即
   fail-fast 的结构化 detail。
"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from backend.core.storage_root import resolve_shared_storage_root
from backend.models.host import Device
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun, PlanRunTargetDevice
from backend.models.project_model import ProjectModel
from backend.models.suite import TestCase, TestSuite
from backend.services.mtbf_suite import content_fingerprint

logger = logging.getLogger(__name__)

class SuiteMaterializationConflict(RuntimeError):
    """物化时点套件内容与门禁通过的冻结基线不一致（#976 / R05-F13）。

    预检门禁与物化之间存在脚本校验等较慢步骤；期间编辑用例会使「已校验内容」
    与物化要注入的启用计数分叉。物化前复核活表指纹，冲突即显式失败而非静默
    消费不一致的计数。
    """


_RUNTASK_NAME = "runtask.xml"
_GLOBAL_NAME = "UiAutomatorTestData.xml"

# #402 守卫的 ACTIVE 集合与 routes/suites.py 共用口径：QUEUED / PRECHECK /
# RUNNING。PRECHECK 也算在途——它马上要物化并消费工具目录文件。
ACTIVE_RUN_STATUSES = ("QUEUED", "PRECHECK", "RUNNING")


def suite_case_rows(db: Session, suite_id: int) -> list[dict]:
    """按 ordinal 取用例行（``content_fingerprint`` 的输入形状）。

    显式查询而非走 ``suite.cases`` 关系：会话若配 ``expire_on_commit=False``
    （测试 conftest 即如此），提交后已加载的集合不会失效，identity map 会把
    过期集合喂给指纹计算——指纹一旦算在陈旧集合上，「库改了没导出」就漏检。
    """
    rows = (
        db.query(TestCase)
        .filter(TestCase.suite_id == suite_id)
        .order_by(TestCase.ordinal, TestCase.id)
        .all()
    )
    return [
        {
            "name": c.name,
            "ordinal": c.ordinal,
            "times": c.times,
            "enabled": c.enabled,
            "exec_descs": c.exec_descs or [],
        }
        for c in rows
    ]


def enabled_case_count(db: Session, suite_id: int) -> int:
    """启用用例计数——注入参数 ``expected_testpoint_count`` 的权威来源。"""
    return int(
        db.execute(
            select(func.count())
            .select_from(TestCase)
            .where(TestCase.suite_id == suite_id, TestCase.enabled.is_(True))
        ).scalar_one()
    )


def current_content_fingerprint(db: Session, suite: TestSuite) -> str:
    return content_fingerprint(
        root_config=suite.root_config,
        global_params=suite.global_params,
        cases=suite_case_rows(db, suite.id),
    )


def resolve_export_dir(suite: TestSuite) -> str:
    """导出目录：显式 export_dir > 项目 key > ``legacy``（兼容 P0 部署现状）。"""
    if suite.export_dir:
        return suite.export_dir
    if suite.project is not None:
        return suite.project.project_key
    return "legacy"


def runtask_disk_path(suite: TestSuite) -> Path:
    """套件在中心存储消费路径上的 runtask.xml（门禁第 2/4 步的对象）。"""
    return (
        Path(resolve_shared_storage_root()) / "mtbf"
        / resolve_export_dir(suite) / _RUNTASK_NAME
    )


def global_disk_path(suite: TestSuite) -> Path:
    """套件在中心存储消费路径上的 Global 文件（#973，与 runtask 同等校验）。"""
    return (
        Path(resolve_shared_storage_root()) / "mtbf"
        / resolve_export_dir(suite) / _GLOBAL_NAME
    )


# ── prepare 冻结（P1 设计 §3.2，与 #401 project/build 同一函数点） ────────────


def freeze_dispatch_suite(db: Session, plan: Plan) -> Optional[dict[str, Any]]:
    """托管模式的准入基线指纹；未绑定返回 None（P0 模式零开销、零字段）。"""
    if plan.suite_id is None:
        return None
    suite = db.get(TestSuite, plan.suite_id)
    if suite is None:
        # FK 保证存在；防御性跳过而非中断 prepare——缺行由五步门禁 fail-fast。
        logger.warning(
            "dispatch_suite_freeze_skip_missing suite_id=%s plan=%s",
            plan.suite_id, plan.id,
        )
        return None
    return {
        "suite_id": suite.id,
        "suite_name": suite.name,
        "exported_sha256": suite.exported_sha256,
        "exported_content_sha256": suite.exported_content_sha256,
        "exported_global_sha256": suite.exported_global_sha256,
        "apk_binding": suite.apk_binding,
        "export_dir": resolve_export_dir(suite),
    }


def step_params_for_dispatch(
    db: Session, dispatch_suite: dict[str, Any]
) -> dict[str, Any]:
    """从冻结块算 mtbf 步骤注入参数（经 STP_STEP_PARAMS 通道下发）。

    - ``expected_testpoint_count``：启用用例数。R05-F13（#976）：门禁与物化间
      存在较慢步骤，期间编辑用例会让「已校验内容」与注入计数分叉——注入前
      复核活表指纹必须等于 prepare/门禁冻结的 ``exported_content_sha256``，
      否则抛 :class:`SuiteMaterializationConflict` 让调度显式失败（可检测）。
    - ``project``：套件 export_dir（替代 host 手工 STP_MTBF_PROJECT env）。
    """
    suite_id = dispatch_suite.get("suite_id")
    if suite_id is None:
        return {}
    frozen_fp = dispatch_suite.get("exported_content_sha256")
    if frozen_fp:
        suite = db.get(TestSuite, suite_id)
        if suite is None:
            raise SuiteMaterializationConflict(
                f"bound suite {suite_id} vanished before materialization"
            )
        current_fp = current_content_fingerprint(db, suite)
        if current_fp != frozen_fp:
            raise SuiteMaterializationConflict(
                "suite content changed between precheck gate and materialization "
                f"(suite_id={suite_id}); re-export and re-dispatch"
            )
    return {
        "expected_testpoint_count": enabled_case_count(db, suite_id),
        "project": dispatch_suite.get("export_dir") or "legacy",
    }


# ── precheck 五步门禁（P1 设计 §3.3）─────────────────────────────────────────


def collect_suite_gate_error(db: Session, pr: PlanRun) -> Optional[dict[str, Any]]:
    """逐项校验；全部通过返回 None，否则返回 fail-fast 结构化 detail。

    套件**身份**优先取 ``run_context.dispatch_suite.suite_id``（prepare 冻结），
    无冻结块时回落 ``plan.suite_id``；二者皆无则放行（存量 P0 / 裸 Run）。
    这样 prepare 后解绑/改绑不会让门禁与物化消费对象分叉（#965）。

    比较基准仍是**该身份对应的活表套件行 + 磁盘文件**——内容指纹按活表校验，
    重导后无需重新 prepare 即可通过门禁；冻结块只锚定「哪一套件」。
    """
    plan = db.get(Plan, pr.plan_id)
    suite_id = _resolve_bound_suite_id(pr, plan)
    if suite_id is None:
        return None
    suite = db.get(TestSuite, suite_id)

    def _fail(step: str, message: str, remedy: str, **extra: Any) -> dict[str, Any]:
        return {
            "step": step,
            "suite_id": suite_id,
            "message": message,
            "remedy": remedy,
            **extra,
        }

    # 1) 存在且 active
    if suite is None or not suite.is_active:
        return _fail(
            "missing",
            f"bound test suite {suite_id} is missing or inactive",
            "rebind the plan to an active suite (PlanUpdate suite_name)",
        )

    # 2) 已导出：两基线列非空 且 磁盘文件存在
    disk_path: Optional[Path] = None
    global_path: Optional[Path] = None
    root = resolve_shared_storage_root()
    if root:
        disk_path = runtask_disk_path(suite)
        global_path = global_disk_path(suite)
    if (
        not suite.exported_sha256
        or not suite.exported_content_sha256
        or not suite.exported_global_sha256
        or disk_path is None
        or global_path is None
        or not disk_path.is_file()
        or not global_path.is_file()
    ):
        # #1560：把「从未导出」与「已导出但基线不完整（含新增列未回填）」区分开。
        # #973 给 Global 基线只加了可空列、迁移不回填，于是本版本发布后**所有**
        # 此前导出过的套件都会落进这一支；而原文案一口咬定
        # "has never been exported"，会把运维引向「套件本身有问题」而不是
        # 「按新版本重导一次」。step 码保持 not_exported（既有契约与 result_summary
        # 消费方不变），改用 message + missing + ever_exported 让现场自助定位。
        missing = []
        if not suite.exported_sha256:
            missing.append("exported_sha256")
        if not suite.exported_content_sha256:
            missing.append("exported_content_sha256")
        if not suite.exported_global_sha256:
            missing.append("exported_global_sha256")
        if disk_path is None or not disk_path.is_file():
            missing.append(_RUNTASK_NAME)
        if global_path is None or not global_path.is_file():
            missing.append(_GLOBAL_NAME)
        # 任一 runtask 侧基线已置 = 这个套件确实导出过；缺口只是补列/文件漂移
        ever_exported = bool(suite.exported_sha256 or suite.exported_content_sha256)
        return _fail(
            "not_exported",
            (
                "exported baseline is incomplete (missing: "
                + ", ".join(missing)
                + ") — re-export to refresh it"
                if ever_exported
                else "suite has never been exported to the tool dir "
                "(or storage root unset)"
            ),
            "run POST /api/v1/test-suites/{id}/export-to-tool-dir",
            export_dir=resolve_export_dir(suite),
            missing=missing,
            ever_exported=ever_exported,
        )

    # 3) 库漂移：「库改了没导出」——指纹是**算出来的**，与端点置空纪律无关
    current_fp = current_content_fingerprint(db, suite)
    if current_fp != suite.exported_content_sha256:
        return _fail(
            "content_changed",
            "library content drifted from the exported baseline (edited but "
            "not re-exported)",
            "re-export via export-to-tool-dir to refresh both baselines",
            expected_content_sha256=suite.exported_content_sha256,
            current_content_sha256=current_fp,
        )

    # 4) 磁盘漂移：「导出后磁盘被人动过」——setup trace 的 suite_sha256 与此闭环
    disk_sha = hashlib.sha256(disk_path.read_bytes()).hexdigest()
    if disk_sha != suite.exported_sha256:
        return _fail(
            "sha_mismatch",
            "runtask.xml on shared storage no longer matches the exported sha",
            "re-export (overwrites the tampered file) or restore the file",
            expected_sha256=suite.exported_sha256,
            disk_sha256=disk_sha,
            disk_path=str(disk_path),
        )

    # 4b) #973 / R05-F10：Global 与 runtask 同等——磁盘丢失/被改即拒绝。
    global_sha = hashlib.sha256(global_path.read_bytes()).hexdigest()
    if global_sha != suite.exported_global_sha256:
        return _fail(
            "global_sha_mismatch",
            "Global (UiAutomatorTestData.xml) on shared storage no longer matches "
            "the exported sha",
            "re-export (overwrites the tampered file) or restore the file",
            expected_sha256=suite.exported_global_sha256,
            disk_sha256=global_sha,
            disk_path=str(global_path),
        )

    # 5) D3b：项目套件必须跑在归属项目的设备上；通用套件（project 空）放行。
    #    v2.5 D10：归属派生（device.model ⋈ project_model 活跃成员行）——
    #    未映射型号（无成员行）与映射到其他项目的型号都算 mismatch，
    #    fail-closed 语义不变。
    if suite.project_id is not None:
        mismatches = db.execute(
            select(
                PlanRunTargetDevice.device_id,
                Device.model,
                ProjectModel.project_id,
            )
            .join(Device, Device.id == PlanRunTargetDevice.device_id)
            .outerjoin(
                ProjectModel,
                and_(
                    Device.model == ProjectModel.match_value,
                    ProjectModel.is_active.is_(True),
                ),
            )
            .where(
                PlanRunTargetDevice.plan_run_id == pr.id,
                or_(
                    ProjectModel.project_id.is_(None),
                    ProjectModel.project_id != suite.project_id,
                ),
            )
        ).all()
        if mismatches:
            return _fail(
                "project_mismatch",
                "target devices belong to a different project than the suite",
                "retarget devices of the suite's project or use a generic suite",
                suite_project_id=suite.project_id,
                mismatched_devices=[
                    {"device_id": did, "device_model": model,
                     "device_project_id": pid}
                    for did, model, pid in mismatches
                ],
            )

    # 6) #975 / R05-F12：最终生效的 mtbf ``project`` 必须与门禁对象
    #    export_dir 一致。注入保留「已有值优先」；冲突覆盖不得静默消费另一目录。
    expected_project = resolve_export_dir(suite)
    for step in (pr.plan_snapshot or {}).get("steps") or []:
        if not isinstance(step, dict):
            continue
        script_name = step.get("script_name") or ""
        if not script_name.startswith("mtbf_"):
            continue
        merged = dict(step.get("default_params") or {})
        overrides = step.get("params")
        if overrides:
            merged.update(overrides)
        declared = merged.get("project")
        if declared in (None, ""):
            continue
        if str(declared) != expected_project:
            return _fail(
                "project_param_conflict",
                "mtbf step project overrides the suite export_dir gated by "
                "precheck",
                "clear the step/script project override or rebind the plan to "
                "the matching suite",
                expected_project=expected_project,
                declared_project=str(declared),
                step_key=step.get("step_key"),
                script_name=script_name,
            )
    return None


def _resolve_bound_suite_id(
    pr: PlanRun, plan: Optional[Plan],
) -> Optional[int]:
    """Run 冻结套件身份优先，其次 live Plan 绑定（#965）。"""
    ctx = pr.run_context if isinstance(pr.run_context, dict) else {}
    frozen = ctx.get("dispatch_suite")
    if isinstance(frozen, dict) and frozen.get("suite_id") is not None:
        try:
            return int(frozen["suite_id"])
        except (TypeError, ValueError):
            logger.warning(
                "dispatch_suite_suite_id_invalid plan_run=%s value=%r",
                pr.id, frozen.get("suite_id"),
            )
    if plan is not None and plan.suite_id is not None:
        return int(plan.suite_id)
    return None


# ── #402 在途守卫（精确匹配版）───────────────────────────────────────────────


def active_run_ids_bound_to_suite(db: Session, suite_id: int) -> list[int]:
    """ACTIVE 且绑定**同一套件**的 PlanRun——覆盖工具目录的硬阻断集合。

    身份口径与门禁一致（#965）：有 ``dispatch_suite`` 时只认冻结
    ``suite_id``；无冻结块时回落 live ``Plan.suite_id``。避免 prepare 后
    解绑逃逸，也避免改绑后把仍消费 A 的 Run 算进 B 的守卫集。
    """
    frozen_hit = PlanRun.run_context.contains(
        {"dispatch_suite": {"suite_id": suite_id}},
    )
    no_freeze = or_(
        PlanRun.run_context.is_(None),
        ~PlanRun.run_context.has_key("dispatch_suite"),
    )
    legacy_hit = and_(no_freeze, Plan.suite_id == suite_id)
    rows = db.execute(
        select(PlanRun.id)
        .outerjoin(Plan, Plan.id == PlanRun.plan_id)
        .where(
            PlanRun.status.in_(ACTIVE_RUN_STATUSES),
            or_(frozen_hit, legacy_hit),
        )
        .distinct()
    ).scalars().all()
    return list(rows)
