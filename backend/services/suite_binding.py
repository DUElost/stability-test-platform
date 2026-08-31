# -*- coding: utf-8 -*-
"""ADR-0030 v1.4 — Plan↔Suite 绑定：冻结 / 注入参数 / precheck 五步门禁。

绑定机制 = ``plan.suite_id`` 可空外键（NULL = P0 文件真源模式，不加门禁；
非空 = 托管模式全门禁）。本模块是绑定语义的**控制面唯一实现**，三组职责：

1. **prepare 冻结**（P1 设计 §3.2）：``freeze_dispatch_suite`` 产出
   ``run_context.dispatch_suite``——准入时刻的基线指纹，供事后归因（D5）。
2. **步骤参数注入**（P1 设计 §3.4）：``step_params_for_dispatch`` +
   ``plan_dispatcher_core.inject_suite_params`` 对 ``mtbf_`` 步骤注入
   ``{expected_testpoint_count, project}``，无需用户声明 default_params。
3. **precheck 五步门禁**（P1 设计 §3.3）：``collect_suite_gate_error``
   按活表套件行 + 磁盘文件逐项校验，任一失败即 fail-fast 的结构化 detail。
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

_RUNTASK_NAME = "runtask.xml"

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
        "apk_binding": suite.apk_binding,
        "export_dir": resolve_export_dir(suite),
    }


def step_params_for_dispatch(
    db: Session, dispatch_suite: dict[str, Any]
) -> dict[str, Any]:
    """从冻结块算 mtbf 步骤注入参数（经 STP_STEP_PARAMS 通道下发）。

    - ``expected_testpoint_count``：启用用例数（物化时点活表计数——此时五步
      门禁已保证 库==导出==磁盘 三方一致，计数不会漂）；
    - ``project``：套件 export_dir（替代 host 手工 STP_MTBF_PROJECT env）。
    """
    suite_id = dispatch_suite.get("suite_id")
    if suite_id is None:
        return {}
    return {
        "expected_testpoint_count": enabled_case_count(db, suite_id),
        "project": dispatch_suite.get("export_dir") or "legacy",
    }


# ── precheck 五步门禁（P1 设计 §3.3）─────────────────────────────────────────


def collect_suite_gate_error(db: Session, pr: PlanRun) -> Optional[dict[str, Any]]:
    """五步逐项校验；全部通过返回 None，否则返回 fail-fast 结构化 detail。

    查找键 = ``plan.suite_id``（join，无 JSON 解析）；plan 未绑定直接放行
    （存量 P0 行为零变化）。比较基准是**活表套件行 + 磁盘文件**——冻结块
    只承担归因，不参与放行判定（重导后无需重新 prepare 即可通过门禁，
    归因差异由 ``run_context.dispatch_suite`` 与 setup trace 的事后比对显性化）。
    """
    plan = db.get(Plan, pr.plan_id)
    if plan is None or plan.suite_id is None:
        return None
    suite = db.get(TestSuite, plan.suite_id)

    def _fail(step: str, message: str, remedy: str, **extra: Any) -> dict[str, Any]:
        return {
            "step": step,
            "suite_id": plan.suite_id,
            "message": message,
            "remedy": remedy,
            **extra,
        }

    # 1) 存在且 active
    if suite is None or not suite.is_active:
        return _fail(
            "missing",
            f"bound test suite {plan.suite_id} is missing or inactive",
            "rebind the plan to an active suite (PlanUpdate suite_name)",
        )

    # 2) 已导出：两基线列非空 且 磁盘文件存在
    disk_path: Optional[Path] = None
    root = resolve_shared_storage_root()
    if root:
        disk_path = runtask_disk_path(suite)
    if (
        not suite.exported_sha256
        or not suite.exported_content_sha256
        or disk_path is None
        or not disk_path.is_file()
    ):
        return _fail(
            "not_exported",
            "suite has never been exported to the tool dir (or storage root unset)",
            "run POST /api/v1/test-suites/{id}/export-to-tool-dir",
            export_dir=resolve_export_dir(suite),
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
                    ProjectModel.match_type == "MODEL",
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
    return None


# ── #402 在途守卫（精确匹配版）───────────────────────────────────────────────


def active_run_ids_bound_to_suite(db: Session, suite_id: int) -> list[int]:
    """ACTIVE 且绑定**同一套件**的 PlanRun——覆盖工具目录的硬阻断集合。"""
    rows = db.execute(
        select(PlanRun.id)
        .join(Plan, Plan.id == PlanRun.plan_id)
        .where(
            PlanRun.status.in_(ACTIVE_RUN_STATUSES),
            Plan.suite_id == suite_id,
        )
        .distinct()
    ).scalars().all()
    return list(rows)
