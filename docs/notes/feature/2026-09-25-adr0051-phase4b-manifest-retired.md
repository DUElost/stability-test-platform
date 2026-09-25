# ADR-0051 Phase 4b：manifest retired 策略落地——新站 catalog 语义可复现（2026-09-25）

Status: implemented
Class: feature

## Decision

落地审查追认的 Phase 4b 数据缺口之二「manifest 退役策略」收口。此前 retired 位全空，
「哪版可选」的运营判断只存在于本站 DB（生产 102 active vs manifest 211 全未退役）——
新站/灾备首扫会把 109 个本站早已退役的旧版重新拉成可选，catalog 语义跨站不成立。

1. **sync 对 retired 条目建行（inactive）**：`plan_snapshot`/`step_trace` 引用的是
   `(name, version)` 字符串，「引用闭合」要求历史行存在——旧逻辑 retired 且无行 = 跳过，
   新站在历史版本上直接断账。现在按包建 inactive 行（登记 sha 无效或包缺失只报告、不建假行）。
2. **109 条 flip `retired:false→true`**（`tool-manifest` 门禁允许的唯一改写）：由
   `tools/dev/manifest_retire_from_db.py` 以本站 active 集物化——dry-run 与手工 SQL 交叉验证
   同为 109，**被 plan_step 引用者拒动（交集 0）**。工具长期保留：catalog 漂移对账
   （「只 PUT 不 flip」的债由它显形）。
3. **文档口径**：`script-versioning` 明确「manifest = 发布级退役真源，PUT = 站点即时态」；
   族树↔最新登记等价、历史 inactive 行按包建，退役动作跨 PR 可审计。

## Alternatives

- **退役策略写进 DB（PUT 即真源）**：弃——DB 是站点态，站点间/灾备无法靠 replay 迁移复现
  「运营判断」；发布单元的可选面必须与发布单元一起被审（manifest PR）。
- **flip 全部非 active 行含 seed-only**：工具天然只动 manifest 条目；生产上 4 条
  seed/legacy 行不在 manifest，不受影响（维持 unregistered 语义）。
- **retired 行也强制回填 package_sha**：现有 retired-skip 分支不回填——inactive 行不进派发
  expected，包身份对它们只是观测属性；33 条 seed-retired 行暂无包 sha，记 Revisit 随
  #3222 观测面一并收。

## Verification

- 安全条件（生产只读）：拟 flip 109 ∩ `plan_step` distinct 引用 = **0**；
  生产 active ⊆ manifest 非退役 ✓（flip 后两边对齐 102）
- `manifest_retire_from_db.py --apply` 写 109 条：`check_tool_manifest --base origin/main` 绿
  （append-only 允许单向 retired）、`check_script_packages` 绿（族树 = 最新非退役条目等价不变）
- **隔离空库重放**（临时库，用后即删）：alembic head → sync：`created=164, conflicts=0`，
  行 (总/活跃/退役有包身份) = **(211, 102, 76)**——新站 active 集 = 生产 102 精确复现，
  历史 retired 行按包存在
- 测试：`test_retired_entries_create_inactive_rows_on_fresh_db`（建行 inactive、身份来自包、
  包缺失只报告）+ sync 15 passed；`check:quick` 16 gates OK（inner-imports 棘轮当场拦下
  2 处函数内 import，已顶层化）
- 生产侧影响：flip 后 scan 走「retired 且行已 inactive」→ 零变化；新 sync 逻辑合入部署后行为不变

## Revisit

- 部署清单（合入后）：新 rev 构建切换 + 重启 + `POST /scripts/scan` 期望
  `created=0/deactivated=0/conflicts=0`（生产已收敛态）。
- 33 条 seed-retired 行无 package_sha（inactive、不进派发）：与 #3222 观测面 PR 一并收。
- fleet 收敛（run 551 空闲窗口）已完成：48/48 `d3d17c8e`——本 PR 合入后的 rev 又需一轮非 force 批量。
