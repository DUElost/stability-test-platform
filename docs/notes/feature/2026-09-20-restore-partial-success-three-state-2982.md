# ADR-0048 v1.1 实施：恢复 PARTIAL_SUCCESS 三态与通过率显示（完成不判红、有失败=黄、abort=红）

Status: implemented
Class: feature

Issue: #2982（裁决载体 ADR-0048 v1.1 修订由 PR #2991 先行提交）

## Decision

owner 2026-09-20 重议落地（三分叉逐项确认，见 ADR-0048 v1.1 §3）：

1. **判定函数恢复三态**：`_resolve_plan_run_status` 加 `failed_only` 参数——
   `aborted > 0 || abort_requested → FAILED`（#783 不变）；`failed_only > 0 →
   PARTIAL_SUCCESS`（黄）；否则 `SUCCESS`（绿）。两个调用点（计数器路径/全量
   扫描路径）入参本就已算好，改动面=判定函数本体。**设备失败永不判红、不断链、
   不触发 RUN_FAILED 的 v1.0 内核完整保留**——恢复的是三态显示，不是阈值轴。
2. **通过率=纯前端派生显示**：列表页「通过率」列由 `result_summary.completed/
   total` 计算（整数百分比，非终态行「—」），与「失败设备」列并存；后端
   `result_summary.pass_rate` 键、`ChainNodeOut.pass_rate`、`failure_threshold`
   列/API/表单、Prometheus histogram 全部**维持废止不回灌**（shape 逐键钉与
   422 钉保持绿色即为证）。
3. **Dashboard 双口径并存**：恢复端点 `GET /stats/plan-run-pass-rate-trend`
   （终态 run 按日 `completed/total` 日均，旧 SQL 形状回植，去 failure_threshold
   残留；PG/SQLite 双方言 + 补零日桶），「运行通过率趋势 (30d)」卡与「失败设备
   数趋势」「方案失败设备数排行」并列挂载。
4. **消费面零改动**：链续跑（TRIGGERABLE 含 PARTIAL）、通知二分（PARTIAL 归
   RUN_COMPLETED）、auto_archive/retention/dedup/recycler/广播、前端 tab/徽标/
   类型——v1.0 D2 保留的面全部原样生效，零迁移。

## Alternatives

1. 仅展示层派生黄徽标（DB 终态仍二值）——否决（owner 分叉①）：显示语义与
   数据脱钩，链/归档/监控仍无法区分「完成有失败」。
2. 三态 + 恢复阈值线（黄线分绿黄）——否决：阈值即 v1.0 判定的「轴错不是数错」，
   且需回灌已 drop 的列/表单。AST 结构钉的防回潮对象相应改写：参数恰好三计数
   （total/threshold 不可入参→比率算式不可能出现）+ 函数体禁除法运算 + 返回
   三终态集合。
3. 回滚 #2750 整体——否决：会把断链修复、失败台数口径、histogram 退役一并
   撤销，且后端 pass_rate 字段回灌违反「唯一权威源=completed/total」。

## Verification

- 判定矩阵：`test_plan_run_aggregation_shared.py` 28 passed（反转
  `test_failed_devices_yield_partial_success_never_failed`、新增绿侧用例
  `test_zero_failed_devices_yield_success`、AST 结构钉 v1.1 改写）。
- 终态化/消费链：job_terminalization + counter_reconciler + chain_trigger +
  abort_race + post_completion + shape_1520 共 65 passed（含端到端刷机失败
  → PARTIAL 断言反转）。
- API 契约：test_stats.py（新增 `TestPlanRunPassRateTrend` 4 例含 days 越界
  422）+ test_plans_api.py 共 97 passed——`failure_threshold → 422` 与
  JobsSummary 逐键钉保持绿色=后端字段未回灌的行为证。
- 消费面扫荡：state_machine/admission/dedup/retention/cron_overlap/catalog/
  plan_runs_api/response-shape-contract 共 325 passed + 1（新端点已登记
  `_MODEL_PAIRS` 双向对拍，15 passed）。
- 前端：`npx tsc --noEmit` clean；`npx vitest run` 定向 5 文件 29 passed
  （列表页通过率列断言、PARTIAL tab 点击用例补缺、趋势图测试回植、analytics
  URL 契约、Dashboard 双图 mock）。
- `python scripts/run_gates.py check:quick`：见提交记录（全绿后提交）。
- 后端全量套件：见本 PR CI（backend-test）。

## Revisit

- 导出 markdown「Pass rate」行、run 详情/链侧栏通过率 meta——owner 本轮未选，
  轻量单即可（ADR-0048 v1.1 §5）。
- PARTIAL 通知文案仍为 RUN_COMPLETED「finished successfully」模板，不含「有
  设备失败」区分；风险看板 `results.py` `success_runs` 仅计 SUCCESS（历史口径）
  ——两处若产生误读再立新裁决。
- 已知代价：568 台常态噪声失败率 4.6–5.2%，多数轮次将为黄。若黄色噪音被证明
  伤害浏览价值，出口是把「黄」降回展示层派生（DB 二值）——需重开本 ADR。
