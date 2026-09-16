# scan 完备性 unit 口径接下游：archive 落 unit 计数 + 前端按 unit 判定 + scan_failed 不粘滞（#2271）

Status: implemented
Class: bug-fix

## Decision

`#1071` 把完备性的计数单位换成 **(host, platform) 对**，但只改了**轮询屏障**；写进终态与
前端展示的仍是 host 级数字。三处收口：

**1. 对口径同时成为下游展示口径。** `record_scan_archive_state` 新增 `units_satisfied` /
`units_expected`（+ `hosts_expected`）落 `run_context.archive`；前端 `buildStages` 的 scan
阶段判定改为「有 unit 字段就用 `units_satisfied / units_expected`，否则回落 host 口径」。
修掉的假绿：host 期望 `{mtk, unisoc}` 只交付 mtk（Agent 侧缺 UNISOC env 键时跳过并只记
本地日志）→ 屏障正确 WARNING，前端却显示 **ok 1/1**。

**2. `hosts_with_artifacts` 语义收窄 + 新增 `hosts_expected`。** 旧口径只看「该 host 有
没有产物」：交了**非期望**平台产物的 host 也算完成。改为「在期望平台内有产物」。
`hosts_expected` 单独给出，因为 `hosts_triggered` 是它的**超集**（无 dedup 平台映射的
host 不进 `expected`，见 `plan_run_scan_scope`）——拿 triggered 当分母永远追不上，凡是含
QCOM-only host 的 run 会**永久** warn。

**3. `scan_failed` 每轮显式重写。** 此前只写 `true`（全仓唯一写入点），一次零产物轮次
之后即使后续补齐，前端仍**永久**显示「扫描未产生任何报表」。改为每轮按对口径判定并
写 true/false 两值：`units_expected > 0 and units_satisfied == 0`——与任务里
`saq_scan_no_artifacts` 的日志判据同一事实源（host 级判据在「期望平台的产物一个都没有、
只有非期望产物」时会漏报）。

`docs/design/2026-scan-upload-merge-contract.md` 的完备性小节按新口径重写（原文声称
「`units_*` 只用于轮询屏障」，与实现不符）。

## Alternatives

- **A. 只补 `hosts_expected`（issue 建议 2 的另一支），展示继续用 host 口径**：否决。
  host 级数字再准也回答不了「该交的 (host, 平台) 是否交齐」——本单的假绿正是它。
- **B. 派生 `scan_failed`（读时按 archive 判定）**：可行但把判据复制到读侧（API/前端各
  一份）；改为**写侧每轮显式重写**，读侧保持 `bool(...)` 不变（零消费面改动）。
- **C. `hosts_with_artifacts` 改成「期望平台**全部**满足的 host 数」**：否决——那是
  `units_satisfied == units_expected` 的 host 级投影，语义与 unit 口径重复；取「至少一个
  期望平台有产物」作为 host 级的存在性判据更清晰。
- **D. 顺手改前端「host 完成度」文案为「平台完成度」**：已做（unit 口径时文案跟着换，
  回落时仍是 host 口径）✓。

## Verification

- **红绿双向**（把 5 个改动文件还原到 `HEAD`）：
  - 后端两条新用例红——`hosts_with_artifacts` 把「只交 mtk 的 host + 交非期望平台的
    host」算成 2（应为 1）；archive 不落 unit 计数且 `scan_failed` 补齐后仍为 true；
  - 前端两条新用例红——`units_expected=2 / units_satisfied=1` 仍显示 ok（host 级
    `hosts_with_artifacts/hosts_triggered` = 1/1），回落用例显示 `host 完成度 3/5`（旧）
    而非 `3/3`（新）。
- **新增用例 4 条**：`test_scan_completeness_counts_only_expected_platform_hosts`、
  `test_record_scan_archive_state_rewrites_scan_failed_every_round`、
  `DedupReportCard` 的 unit 判定与回落各一条。
- **既有断言同步**：`backend/agent/tests/test_saq_scan_pipeline.py` 的 7 条
  `record_archive.assert_called_once_with(...)` 补上新的三个 kwargs（夹具 `_c()` 同时
  补 `hosts_expected`）——**这是本单必须一并做的**，否则「写入侧多传参数」会被旧断言
  判红。
- 测试批次：`backend/tests -k "dedup or scan or archive"` → **250 passed**；
  `tests/test_api_response_shape_contract.py` → **15 passed**（schema 新字段与
  `types.ts` 对齐）；`backend/agent/tests/test_saq_scan_pipeline.py` → **33 passed**
  （带 CI 口径的 gate env）；前端 `vitest run src/components/plan-run` → **116 passed**；
  `tsc --noEmit` 通过；`ruff check backend/` 全绿。
- **未跑**：全量 backend/前端套件（受影响面已按关键词与目录覆盖，其余交 CI）。
- **未做**：`hosts_not_acked` 的 unit 化（它本来就是 host 级事实）；QCOM 等无平台映射
  host 的**归档**归属（`plan_run_scan_scope` 的排除逻辑不变）。

## Revisit

- **老数据的展示面**：`units_*` 是本次新增的键，本次之前的 run 只有 host 级数字 → 前端
  回落口径（`hosts_expected` 也缺时再用 `hosts_triggered`，仍可能有永久 warn 的老 run）。
  要不要为历史 run 回填（一次性作业）取决于是否有人真去看那些 run 的卡片。
- **`scan_failed=false` 的写入面**：现在每轮都写 `result_summary.scan_failed`（含 false）。
  若将来有消费者把「键存在」当「曾失败过」的信号，语义会冲突——本单已把读侧统一为
  `bool(...)`（API `dedup.py` 与前端都是布尔的直接消费）。
- **unit 口径的下一层**：目前 unit = (host, platform)，与「设备数」无关。若将来要求
  「每台设备的报表」，单位还要再细分一层——那时 `ScanCompleteness` 需要按 (host,
  platform, device) 或按产物完整性判定，属独立议题。
