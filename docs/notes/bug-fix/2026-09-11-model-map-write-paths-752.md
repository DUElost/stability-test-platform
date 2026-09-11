# 型号映射写路径残余缺口（#752 / R04-F07）

Status: implemented
Class: bug-fix

## Decision

#644（大小写归一）与 #659（归档守卫）之后，写路径仍有三处未对齐（复核基线
`dfe4895a` 仍在）：

1. **bulk_assign 混大小写 500**：`devices.py` 的成员行查重仍是
   `match_value == model` 全等；设备事实与既有成员行大小写不同（如成员行
   `INFINIX_X1102D`、设备 `Infinix_X1102D`）→ 全等 miss → INSERT 撞
   `uq_project_model_active`（lower 部分唯一索引）→ **未捕获 IntegrityError →
   500**。修复：与 `apply_project_map` 对齐——`func.lower` 归一匹配 +
   `db.flush()` 捕 `IntegrityError` → 409（并发双写兜底）。
2. **bulk_assign 无归档守卫**：可对 ARCHIVED 项目写新 ACTIVE 成员行，
   等价于用 SEED 标签复活归档。修复：入口加与
   `projects.py::_require_active_project` 同口径的 409 守卫（**内联**——
   仓库无跨路由 import 私有 helper 的先例，4 行重复换模块解耦；若后续第三个
   入口需要该守卫再抽共享模块，见 Revisit）。
3. **apply 同项目大小写变体不收敛**：`existing` 归一命中且属于本项目时 INSERT
   整体跳过 → 200 且 `will_assign>0`，但成员行大小写永不收敛到设备事实 →
   join 全等 miss → 设备**静默未归属**（症状从 500 变成静默 200）。修复：
   同项目分支把 `match_value` 覆写为设备事实原值（`model_facts`），与
   「新行写设备事实原值」原则一致。

## Alternatives

- **apply 同项目改「明确报错要求先删后映射」**：会破坏预览承诺（预览说
  will_assign>0，apply 却报错），且运维要手工删行——收敛覆写更贴近既有语义；
- **从 projects.py import `_require_active_project`**：无先例的跨路由私有
  导入，评审面大于 4 行内联；且两处口径需各自测试锁定（本单已加归档用例）；
- **bulk 也做大小写覆写**：issue 未列该缺陷（bulk 命中即幂等跳过），保持
  最小改动，不引入第四条行为差异。

## Verification

实际运行：

- `pytest backend/tests/api/test_project_routes.py -q` → **75 passed**
  （新增 4 例：bulk 混大小写 → 409 而非 500；归档项目批量归入 → 409 且零成员
  行；bulk 并发 flush 撞唯一索引 → 409；apply 同项目大小写变体行收敛为设备
  事实原值）；
- `ruff check backend/api/routes/devices.py backend/api/routes/projects.py
  backend/tests/api/test_project_routes.py` → All checks passed；
- `python scripts/run_gates.py check:quick` → 7 gates 全绿。

未完成（pending）：

- 无（三个缺陷各有对应用例；设备侧读端大小写并存是 #704，与本单互补、另行
  跟踪）。

## Revisit

- 若第四个写路径入口需要归档守卫，把 `_require_active_project` 提升到共享
  模块（如 `backend/services/project_guard.py`），本单内联副本随之收敛；
- #704（读端 `func.lower` 缺失导致大小写并存）仍开放：本单只修写路径，
  读端未对齐时归因会呈现为「设备已在库但筛选不到」，排查时先看 #704。
