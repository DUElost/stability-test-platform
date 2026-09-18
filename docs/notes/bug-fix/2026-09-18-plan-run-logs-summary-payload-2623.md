# 日志页改吃 summary 端点：两个标量不该付全量 jobs 的钱（#2623）

Status: implemented
Class: bug-fix

关联：[#703](https://github.com/DUElost/stability-test-platform/issues/703)（同一条失效路径的放大器）、
[ADR-0047](../../adr/ADR-0047-db-pool-and-connection-capacity.md)（池与上限的容量取向，Proposed）、
#1520（summary 端点的正规化来源：`test_plan_run_shape_1520.py`）。

## Decision

1. **只做 #2623 的最小方案**：`build_plan_run_summary` 补 `plan_name`，`PlanRunLogsPage.runQ`
   从 `api.planRuns.get`（detail，内嵌全量 jobs）改到 `api.planRuns.getSummary`（371 B 级聚合）。
   **不动 `PlanRunDetailOut.jobs`**——那是该单第 3 条、方向级、需先清点仓外调用方（本单没做那项清点，
   所以不碰）。
2. **`plan_name` 的口径提出来复用，而不是复制一份查询。** detail 一直用
   `select(Plan.name).where(Plan.id == pr.plan_id)`；若 summary 另写一份、或图省事去读
   `plan_snapshot["name"]`，就造出「同一字段两个口径」（快照不跟随改名）。
   故新增 `services/plan_run_read_common.resolve_plan_name(db, pr)`，**detail 与 summary 同源**，
   detail 行为逐字不变。
3. **queryKey 用独立的 `planRunKeys.summary(id)`**，并同步 `refreshAll` 里失效的键。
   两个形状不同的响应共用 `['plan-run', id]` 会让缓存互相覆盖；而漏改失效键的后果更隐蔽——
   「刷新」按钮照常可点、照常转圈，但**没有任何查询会重取**（本单为此专门加了一条用例）。
4. **`PlanRunSummary.status` 顺手由 `string` 收紧为 `PlanRunStatus`**（与 detail 同一口径）。
   这不是美化：页面里 `TERMINAL: ReadonlyArray<PlanRunStatus>` 的 `.includes(status)` 在
   `status: string` 下**编译期直接报错**——收紧之后，"终态判定"这条控制流第一次被类型系统看着。
   （发现方式就是本次 `tsc --noEmit`，不是设计出来的。）
5. **响应模型的键集合由既有的双向对拍强制**：`tests/test_api_response_shape_contract.py::_MODEL_PAIRS`
   里已登记 `PlanRunJobsSummaryOut ↔ PlanRunSummary`，后端加字段而 `types.ts` 不同步 → 当场红。
   顺带更正一个我一度以为的缺口：#2032 的单向守卫 `test_frontend_api_types_sync.py` 只登记了
   Host/Device（且它的解析自守要求模型有 `id` 字段，`PlanRunJobsSummaryOut` 用的是 `plan_run_id`，
   直接登记会先撞自守断言）——**这个模型的对拍其实早就在**，只是不在我以为的那个文件里。

## Alternatives

- **加 `?include_jobs=false` 让 detail 可选瘦身**：否决（本单内）。它把"谁该付钱"的决定推给调用方，
  且真正的问题（仓外是否有调用方依赖内嵌 jobs）没清点之前不动 detail 契约更安全；#2623 第 3 条已把它列为另议。
- **日志页继续用 detail，只把 `refetchInterval` 调长**：否决。#823 已经处理过"终态后停更"，
  剩下的正是**单次载荷**；把轮询调长只稀释症状，还会让终态判定更迟钝。
- **在前端把 `jobs` 从 detail 响应里删掉再缓存**：否决。字节已经过线（网络与序列化都付了），
  且会让"缓存里的 run 对象"与"接口返回的对象"两种形状长期共存。
- **不补 `plan_name`，让页面从 detail 拿标题、从 summary 拿状态（两个查询）**：否决。
  为一个标量保留第二条重查询，等于把本单的收益砍掉一半。

## Verification

- 后端：`pytest backend/tests/api/test_plan_run_shape_1520.py -q` → **6 passed**（新增 3 条）。
- 前端：`vitest run src/pages/execution/PlanRunLogsPage.test.tsx` → **16 passed**（新增 3 条 +
  把 5 处 CSV 用例的终态驱动从 detail 桩改成 summary 桩）；全量 `vitest run` → **128 files / 1026 tests passed**；
  `tsc --noEmit`、`eslint src --max-warnings 0` 通过；`tests/test_api_response_shape_contract.py` +
  `tests/test_frontend_api_types_sync.py` + `test_read_api_auth.py` 合跑 **95 passed**。
- **四处判伪（回退实现、保留用例）**：
  1. **两侧都改读 `plan_snapshot`**（一致但错误）→ 只有"改名判据"红（`1 failed / 5 passed`），
     单纯对拍 `summary==detail` **仍绿**（`1 passed`）。这条是本单最重要的一条自证：
     它证明我加的不是"两边相等"这种弱断言——两个都错的实现同样能满足相等。
  2. summary 服务漏填 `plan_name` → 键集合与对拍 **2 failed**；
  3. 页面退回 `api.planRuns.get` + detail 键 → 前端 **6 failed**；
  4. 只切 `queryFn`、漏改 `refreshAll` 的失效键 → 前端 **1 failed**（正是那条"刷新静默失效"用例）。
  恢复后 6 / 16 全绿。
- **未复测字节数**：`193,866 / 196,041 B ≈ 98.9%`、`510 job / 30 host`、`371 B` 均引自 #2623 的
  dev 隔离栈实测与取证脚本，本单没有再造 510 job 的现场。本单能证明的是**结构事实**：
  summary 响应键集合里不含 `jobs`，detail 含且页面此前每次都取——该断言已写进用例（第 2 条判伪里它一起红）。

## Revisit

- **detail 内嵌全量 jobs 仍在**（#2623 第 3 条）。真要动它，前置是仓外调用方清点（同 ADR-0045 D6 的做法：
  先证明 `deploy/`、工具、脚本零引用，再决定"不填充"还是 `?include_jobs=false`）。
  选机工作台（`PlanExecutePage`）目前还并行拉最多 3 条 detail 只为读 `run_context.dispatch_device_ids`——
  那是下一个同型收益点，但同样要先回答"detail 该不该带 jobs"。
- **`PlanRunJobsSummaryOut` 的名字与前端 `PlanRunSummary` 不一致**是 #1520 有意为之的
  （OpenAPI component key 消歧，#82 教训），别顺手"统一命名"。
- 若 #703 ②（ADR-0047）裁决为"收预算 + 快失败"，这类**轮询载荷**问题会从"性能优化"升级为
  "容量预算的一部分"：届时应把 detail/summary 的载荷上限写成判据（例如响应字节 P99 指标），
  而不是靠每次人工发现某个页面在拉 196 KB。
