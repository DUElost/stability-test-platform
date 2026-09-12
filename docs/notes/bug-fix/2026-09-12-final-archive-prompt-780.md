# Agent Note: 最终归档提示的两个错位修正（#780）

Status: implemented
Class: bug-fix
Issue: #780

## Decision

三处改动，都是把前端判据对齐到后端**实际可执行性**：

1. **删掉幻影字段读取**。原判据是
   `archiveReadiness?.ready ?? watcherQ.data?.archive?.ready_for_extract ?? false`，
   而后端 `WatcherArchiveOut`（`api/schemas/plan_run.py:354-361`）只有
   `scan_status` / `ops_metrics` / `scan_triggered_at` / `signaled_jobs` /
   `pending_jobs` / `failed_jobs` / `link_stats` —— **没有任何 `readiness` /
   `ready_for_extract`**。故该表达式恒为 `false` → `finalArchiveReady` 恒 false
   → 最终归档提示**永不弹出**（死功能）。改用真实键 `archive.scan_status`。
2. **触发状态判据从 FAILED 改为 SUCCESS / PARTIAL_SUCCESS**。原 effect 是
   `if (status !== 'FAILED') return`，即只在失败/中止态提示；而后端
   `trigger_extract`（`api/routes/dedup.py:669-672`）对 FAILED **无条件 409**
   （`PlanRun FAILED：按 ADR-0028 D2 不执行 extract`，实测无人工旁路）——
   提示出来的动作必定失败。改为后端会接受的两个终态。
3. **弹窗文案同步**。原文案首句是「测试已中止或失败，系统不会自动归档」，
   与新判据（成功终态）矛盾；同时删掉对幻影 `readiness.reason` 的插值展示。

判据收口为三个真实条件同时成立：
`capabilities.final_archive`（= 终态，后端产出）∧ `status ∈ {SUCCESS, PARTIAL_SUCCESS}`
∧ `archive.scan_status === 'merged'`（`run_extract_sync` 缺 merge 结果会 409）。

## Alternatives

- **只删幻影字段、保留 FAILED 触发**：否决。那是把「永不弹」换成「弹了也必定 409」，
  并没有解决问题（后端对该状态无条件拒绝）。
- **只删幻影、判据全靠 `capabilities.final_archive`**：否决。后端该字段的定义是
  `"final_archive": terminal`（`api/routes/plan_runs.py:216`）——对 FAILED 同样为
  true，单独使用无法排除会被 409 的状态。
- **顺手修改后端 `capabilities.final_archive` 的语义（排除 FAILED）**：否决（本次）。
  该字段可能被其它消费方依赖，改它属另一条变更面；已记 Revisit。
- **把提示整个删掉（改由 DedupReportCard 手动入口承担）**：否决。提示是既有功能
  （sessionStorage 一次性防重），本次只需让它按真实条件触发。

## Verification

- `npx vitest run src/pages/execution/PlanRunDetailPage.test.tsx` → **23 passed**，
  含新增 3 例：
  - 正例：`SUCCESS` + `scan_status='merged'` + `final_archive` → 提示弹出；
  - 反例：`FAILED` + `merged` → **不弹**（后端会 409）；
  - 反例：`SUCCESS` + `scan_status='scanned'` → **不弹**（缺 merge 结果会 409）。
  原用例的夹具已从幻影形状（`readiness: { ready, reason }` 并断言展示
  `merge complete`）改成后端真实形状——原夹具正是 issue 指出的「盲区自洽」。
- `python scripts/run_gates.py check:quick` → 7 gates 全绿。
- **未验证（诚实标注）**：端到端真机/联调（需 NFS 共享存储与真实 merge 产物）；
  `scan_status` 的取值来源是后端 watcher-summary 聚合（`api/routes/plan_runs.py:2632-2656`），
  本次按前端消费口径对齐，未新增后端用例。

## Revisit

- **后端自身语义冲突**（本次未动，建议单独立项）：`capabilities.final_archive` 定义为
  `terminal`，而 `trigger_extract` 对 FAILED 无条件 409——后端同时对外宣称「FAILED 可
  最终归档」与「FAILED 不可 extract」。前端现按**强制力更强的一侧**（409 是执行时的
  实际拒绝）收口，但后端两处权威应对齐（或让 capability 排除 FAILED，
  或让 extract 对 FAILED 开放人工通道，需产品裁定）。
- **与自动归档的关系**：`auto_archive_sweep`（#833）已覆盖部分终态 run 的自动扫描/合并；
  本提示针对「已 merge 但未归档」的情形。若后续自动归档覆盖面扩大，应重新评估该提示
  是否仍必要（避免对用户重复提示）。
