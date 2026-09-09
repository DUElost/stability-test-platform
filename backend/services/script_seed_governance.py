"""种子迁移治理模板（R03-F10/#942，裁决 = A 引用感知、遇引用即失败）。

**权威源 = 本文件**。未来的种子迁移必须把
``raise_if_version_referenced`` / ``raise_if_any_version_referenced`` 的
当前实现**内嵌**进迁移文件（迁移自包含、不 import 服务层——迁移是冻结的
历史，服务层会演进）；`docs/development/script-versioning.md`
「种子迁移治理」节是义务条文。

规则：
- 种子迁移对**已存在**的 ``(script_name, script_version)`` 做任何写操作
  （UPDATE ``default_params``/``param_schema``、停用 ``is_active`` 等）前，
  必须先查 ``plan_step`` 引用；
- 引用数 > 0 → ``RuntimeError``（迁移失败，操作者重指 plan_step 或新建版本
  表达参数变化后重跑）——与 API 层 ``_ensure_script_can_be_deactivated``
  的 409 语义同构；
- 全新版本行的 INSERT 不受此约束。

背景实证：``b8c9d0e1f2a3``（原地 UPDATE default_params）、
``k1l2m3n4o5p6``（无引用停用 v1.0.3）——两者均在服务层保护之外
（裁决 note：docs/design/2026-09-08-seed-migration-governance.md）。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text


def count_plan_step_references(
    conn: Any, *, script_name: str, script_version: str
) -> int:
    """plan_step 中引用该 (script_name, script_version) 的步骤数。"""
    return int(
        conn.execute(
            text(
                "SELECT COUNT(*) FROM plan_step "
                "WHERE script_name = :name AND script_version = :ver"
            ),
            {"name": script_name, "ver": script_version},
        ).scalar_one()
    )


def raise_if_version_referenced(
    conn: Any, *, script_name: str, script_version: str
) -> None:
    """种子迁移写已存在版本前的强制闸：有 plan_step 引用即失败。"""
    n = count_plan_step_references(
        conn, script_name=script_name, script_version=script_version
    )
    if n > 0:
        raise RuntimeError(
            f"seed migration aborted: {script_name} {script_version} 仍被 "
            f"{n} 个 plan_step 引用——种子迁移不得原地覆写/停用被引用版本。"
            "请把引用的 plan_step 重指到新版本，或以新建版本表达参数变化，"
            "然后重跑迁移。"
        )


def raise_if_any_version_referenced(
    conn: Any, *, script_name: str, versions: list[str]
) -> None:
    """批量形态（deactivate 列表场景）：任一版本被引用即失败并列出全部。"""
    blocked: list[tuple[str, int]] = []
    for ver in versions:
        n = count_plan_step_references(
            conn, script_name=script_name, script_version=ver
        )
        if n > 0:
            blocked.append((ver, n))
    if blocked:
        detail = ", ".join(f"{script_name} {v} ×{n}" for v, n in blocked)
        raise RuntimeError(
            f"seed migration aborted: 待停用版本仍被 plan_step 引用：{detail}。"
            "请重指 plan_step 到在用版本后重跑迁移。"
        )
