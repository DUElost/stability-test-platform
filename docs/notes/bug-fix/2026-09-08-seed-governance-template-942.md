# 种子迁移治理模板落地（#942，裁决 A）

Status: implemented
Class: bug-fix

## Decision

按裁决 note（`docs/design/2026-09-08-seed-migration-governance.md`，PR
#1163）的选项 A 落地「引用感知、遇引用即失败」：

1. **治理模板权威源** `backend/services/script_seed_governance.py`：
   `count_plan_step_references` / `raise_if_version_referenced` /
   `raise_if_any_version_referenced`（批量停用形态，错误列出全部被堵版本）；
2. **义务条文**写入 `docs/development/script-versioning.md`「种子迁移治理」
   节：对已存在版本做任何写操作前必须查 `plan_step` 引用、引用即失败、
   禁止裸 UPDATE `default_params` / 无引用停用；迁移**自包含**原则下模板以
   **内嵌复制**方式进迁移文件（权威源只做被测的复制来源）；
3. **裁决 note §5 修正**：原计划的「共享辅助模块 import」与迁移自包含原则
   冲突（迁移是冻结历史，服务层会演进）——改为内嵌模板，本 PR 一并修正
   （裁决方向 A 不变）；
4. 既有两个种子迁移不改（已执行，重写违反 ADR-0008）。

## Alternatives

- **共享模块 import 进迁移**——放弃（修正裁决 note 原案）：迁移重跑会绑定
  演进后的服务层代码，破坏迁移冻结性；
- **跳过+告警（选项 B）**——裁决已否：静默分叉正是 #942 指控形态。

## Verification

- `tests/test_script_seed_governance.py` **3 passed**（testcontainers 真
  Postgres + 最小建表）：无引用放行 / 有引用失败且指引文案可读（含计数）/
  批量停用形态列出全部被堵版本；
- `check:quick` 7 门禁全绿。

## Revisit

- 未来首个使用该模板的种子迁移落地时，在 Agent Note 注明内嵌复制来源与
  复制日期，便于审计漂移；
- #906（ADR-0035）同轮转 Accepted，其实施单独立。
