# 审计 resource_type 双词收敛到「表名」规范（#2778）

Status: implemented
Class: bug-fix

## Decision

规范成文：`resource_type := 实体对应的表名`；无独立表的实体（session / wifi / task /
agent_dead_letter 等）在 `backend/core/audit.py` 的 `AUDIT_RESOURCE_TYPES` 显式登记，
登记即视为规范值。三层落地：

1. **写侧统一为规范值**：`agent_completion.py` 三处（`stale_job_completion_rejected` /
   `terminal_payload_conflict` / `job_terminalized`）`job → job_instance`；
   `scripts.py` 的 scan 一处 `script_catalog → script`。
2. **读侧归并**（审计行 append-only，历史行不改）：`AUDIT_RESOURCE_TYPE_ALIASES` +
   `canonical_resource_type()` / `expand_resource_type_filter()`；列表过滤按
   「规范值 + 其别名」展开 `IN`；facets 资源维按规范值合并计数并重排
   （`_merge_alias_facets`），下拉里同一实体只出现一项，不再并列两个"半真选项"。
3. **机械守卫**（本单真正的落点）：`backend/tests/test_audit_resource_type_guard.py`
   两条离线 AST 断言——①写侧 `resource_type` 字面量必须 ∈ 规范词表且**不得使用别名**；
   ②非通用动作（排除 create/update/delete/deactivate/export/import）的
   action→resource_type 必须唯一。另加别名表自身一致性断言（别名不重叠规范值、
   目标已登记）。

选「读侧归并 + 写侧统一」而不是「回填历史行」：审计是持久证据（ADR-0044 D3），
历史行不可追溯改写；且回填不阻止未来再分裂，守卫才阻止。

## Alternatives

- **只把 recycler 改成 `job`（或反向选边）**：#2778 已否决——选边不立规范，下一个
  新实体仍会靠猜，下一次分叉照旧。
- **数据迁移回填历史行**（`UPDATE audit_logs SET resource_type='job_instance' ...`）：
  违背 append-only 证据语义，且不解决"未来再写一个别名"的根因。
- **DB 层「同一 action 唯一 resource_type」约束**：全量 AST 扫描证明 `create` /
  `update` / `delete` 等通用动词天然跨 8~9 个实体（合法形态），约束不可行；守卫因此
  显式排除通用动词，只对非通用动作断言唯一。
- **把词表做成 Python Enum / DB 枚举列**：写侧存在动态取值（resource_pools 的
  `p.resource_type`）与历史别名共存，enum 收不住；登记式 frozenset + 守卫已达成
  「新值必须先登记」的目的，且不引入迁移。

## Verification

- `python -m pytest backend/tests/test_audit_resource_type_guard.py -q` → **3 passed**；
- **反向验证（守卫有牙）**：把 `agent_completion.py` 任一处改回 `resource_type="job"`
  → `test_write_side_resource_types_are_registered_canonical_values` FAILED
  （1 failed, 2 passed），随后还原；把 `recycler.py` 的 `job_terminalized` 改为
  `plan_run` → `test_non_generic_actions_use_a_single_resource_type` FAILED
  （1 failed, 2 passed），随后还原（`git diff` 无残留）；
- `python -m pytest backend/tests/api/test_audit.py -q` → **18 passed**（含新增
  «facets 归并历史别名» 与 «过滤展开历史别名» 两用例）；
- `python -m pytest backend/tests/api/test_agent_dual_write.py -q` → **75 passed**
  （`:880` 既有断言同步为 `job_instance`）；
- `python -m pytest backend/tests/api/test_scripts.py -q` → **30 passed**；
- **AST 全量扫描**（`backend/` 排除 tests/alembic）：真实分叉仅
  `job_terminalized`（job / job_instance）一处；`script_catalog` 仅存在于动态 action
  的 scan 调用点；写侧别名现存 0 处。
- `python scripts/run_gates.py check:quick` → **[OK] check:quick（12 gates）**。

## Revisit

- **新增资源类型**：必须在 `AUDIT_RESOURCE_TYPES` 登记，否则守卫直接红——这是设计意图，
  不是阻碍；无表实体在登记处补一行注释说明其派生面。
- **若将来决定回填历史行**（如审计导出面要求单一口径）：先由 ADR 修订 ADR-0044 D3 的
  不可篡改边界，再撤别名与归并逻辑；在此之前不得写 UPDATE。
- **前端 `RESOURCE_LABELS` 的 `script_catalog: '脚本目录'`** 已不再作为 facets 值出现
  （历史行仍可能以该值展示于列表），保留为展示名即可，无需跟改。
- 触发条件：若再出现「同一实体跨路径两个资源类型」的新分叉，按本单同一模式收口
  （别名登记 + 读侧归并 + 写侧统一），不要新造第二套归并机制。
