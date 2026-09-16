"""重放 #2055 对两个已合入 seed revision 的原地改写（#2322）

Revision ID: d4e5f6a7b8c9
Revises: p6q7r8s9t0u1
Create Date: 2026-09-16

``02941d3c``（#2055）在**已合入 main 之后**原地改写了两个 revision 的**函数体**
（不是新增文件），却没有按 ``check_alembic_revision_immutability.py`` 契约附重放
迁移：

- ``z1a2b3c4d5e6``（seed monkey_launch v5.0.2）：目标行已存在时补
  ``is_active = true``——此前该分支**什么都不做**，于是「旧版本被停用、新版本也不
  是 active」的空档不会被修掉（precheck 只取 ``is_active``，相关 plan 要等到
  precheck 才失败）；
- ``y0z1a2b3c4d5``（seed gpu_setup v1.0.10）：停用 1.0.9 前补 ``plan_step`` 引用
  核对（#942 裁决 A「禁止无引用检查的 ``is_active=false``」）。

对在这两个 revision 进入 main **之后**才 ``alembic upgrade`` 的库，改后的函数体
**永远不会再执行**：Alembic 不校验 ``*.py`` 内容，``alembic_version`` 仍是 head，
``tools/dev/check_alembic_at_head.py`` 的等值判定照样判绿——库缺陷与「已对齐」在
护栏看来一样。

本迁移把这两件事对「已执行过旧代码的库」重放一次，均为 ``WHERE … 命中才写`` 的
幂等自愈（先例：#1717 的 ``f6a5b4c3d2e1``）：

- **monkey_launch**：仅当该脚本**没有任何 active 版本**时把 5.0.2 置回 active——
  这是旧代码会留下的确切缺陷态；管理员有意停用且另有 active 版本时**不**动它
  （比修正后的 revision 更窄：不为「重放」反向覆盖人工决策）；
- **gpu_setup**：对 1.0.9 重跑引用核对，仍被 ``plan_step`` 引用即 **raise**——
  把「静默停用」变成部署期可见的失败，与修正后的 revision 同判据同措辞。

**不重放** ``downgrade`` 语义的改写（``z1a2b3c4d5e6`` 的 DELETE → 翻转
``is_active``）：downgrade 只影响由 head 向下走的库，与「已 upgrade 的库漏跑修复」
无关；且本迁移自身的 ``downgrade`` 是 no-op（自愈没有逆操作，翻回去只会制造新空档）。

迁移自包含（不 import 服务层），核对判据与 ``y0z1a2b3c4d5`` 的内嵌实现逐字一致。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from alembic import op
from sqlalchemy import text

revision = "d4e5f6a7b8c9"
down_revision = "p6q7r8s9t0u1"
branch_labels = None
depends_on = None

#: ``z1a2b3c4d5e6`` 的目标（新）版本——旧代码在行已存在时不置 active。
_MONKEY_LAUNCH_NAME = "monkey_launch"
_MONKEY_LAUNCH_TARGET = "5.0.2"

#: ``y0z1a2b3c4d5`` 停用的版本——旧代码停用前未做引用核对。
_GPU_SETUP_NAME = "gpu_setup"
_GPU_SETUP_DEACTIVATED = ["1.0.9"]


def _count_plan_step_references(conn: Any, script_name: str, versions: list[str]) -> list[str]:
    """返回仍被 ``plan_step`` 引用的版本（形如 ``"1.0.9 ×2"``）。

    与 ``y0z1a2b3c4d5`` 的内嵌实现同一判据（#942：迁移自包含，不 import 服务层）。
    """
    blocked: list[str] = []
    for ver in versions:
        n = int(
            conn.execute(
                text(
                    "SELECT COUNT(*) FROM plan_step "
                    "WHERE script_name = :name AND script_version = :ver"
                ),
                {"name": script_name, "ver": ver},
            ).scalar_one()
        )
        if n > 0:
            blocked.append(f"{ver} ×{n}")
    return blocked


def upgrade() -> None:
    conn = op.get_bind()
    now = datetime.now(timezone.utc)

    # ① monkey_launch v5.0.2：只在「一个 active 版本都没有」时自愈。
    conn.execute(
        text(
            "UPDATE script SET is_active = true, updated_at = :now "
            "WHERE name = :name AND version = :ver "
            "  AND NOT EXISTS ("
            "    SELECT 1 FROM script AS other "
            "    WHERE other.name = :name AND other.is_active"
            "  )"
        ),
        {"name": _MONKEY_LAUNCH_NAME, "ver": _MONKEY_LAUNCH_TARGET, "now": now},
    )

    # ② gpu_setup：仍被引用即失败（与修正后的 revision 同判据同措辞）。
    blocked = _count_plan_step_references(conn, _GPU_SETUP_NAME, _GPU_SETUP_DEACTIVATED)
    if blocked:
        raise RuntimeError(
            "seed migration aborted: 待停用版本仍被 plan_step 引用："
            f"{_GPU_SETUP_NAME} {', '.join(blocked)}。"
            "请把引用的 plan_step 重指到新版本后重跑迁移。"
        )


def downgrade() -> None:
    """no-op：自愈类重放没有逆操作。

    把 monkey_launch 5.0.2 翻回 inactive 只会重新制造「无 active 版本」的空档；
    gpu_setup 侧本迁移不写任何行。与 ``f6a5b4c3d2e1``（#1717）同形态。
    """
