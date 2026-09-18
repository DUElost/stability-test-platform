# ADR-0048 实施：移除 run 级测试通过率判定（完成即绿、abort 才红）

Status: implemented
Class: simplification

Issue: #2734（裁决载体 ADR-0048 由 PR #2738 先行合入）

## Decision

执行状态语义 v2 落地（owner 2026-09-18 裁决，三分叉逐项确认）：

1. **判定轴收窄**：`_resolve_plan_run_status` 只剩 abort 轴——`aborted > 0 ||
   abort_requested → FAILED`，其余（全部 job 落终态）一律 `SUCCESS`。设备失败
   台数不再改变 run 状态，以 `failed_job_count`/`result_summary.failed` 事实
   呈现；`PARTIAL_SUCCESS` 不再产出（枚举行态、状态机吸收边、终态集合六处、
   retention/auto_archive/dedup 消费面全部保留给存量历史行）。
2. **failure_threshold 全链移除**：`plan`/`plan_run` 两列 + CHECK 约束
   （alembic `f6a7b8c9d0e1`，接 `a1b2c3d4e5f7` 之后）、API Create/Update/
   Out/Trigger（extra=forbid 下拒绝旧字段 422）、快照组装、派发冻结、迁移
   预检校验项、schema 基线；#1591-④ 里程碑豁免整体删除（其目标问题在新语义
   下不存在）。
3. **「通过率」指标删除、失败台数事实保留**：`result_summary.pass_rate`、
   JobsSummary/ChainNode `pass_rate`、导出「Pass rate」行、Prometheus
   `stability_plan_run_pass_rate` histogram、watcher 摘要 exceeded/threshold
   标志、前端表单输入/详情页 meta/列表「通过率」列（→「失败设备」列）。
   Dashboard 两图替换：「方案成功率排行」→「方案失败设备数排行」
   （`/stats/plan-failed-devices`）、「运行通过率趋势」→「失败设备数趋势」
   （`/stats/plan-run-failed-device-trend`，SUM(failed) 事实口径）；链侧栏
   节点展示 `failed_jobs`。

## Alternatives

1. 阈值放宽（0.08–0.1）——否决：噪声基线随 fleet 组成漂移，是轴错不是数错
   （run 428 以 5.2% 越 5% 线断链为实证）。
2. drop PARTIAL_SUCCESS 枚举（含历史行改判）——否决：给历史行编造状态，
   DB enum 迁移成本与风险不成比例；存量随 retention（3 天）自然消失。
3. 任一设备失败即 FAILED——否决：与「施压平台无通过率要求」矛盾，链断更频。

## Verification

- 判定矩阵重写：`test_plan_run_aggregation_shared.py` 27 passed（新增
  `test_resolve_plan_run_status_signature_has_no_threshold_axes` AST 结构
  钉 + `test_milestone_probe_is_gone` 防回潮 + 设备失败不改状态参数化用例）。
- 受影响面全绿：aggregation_endpoints/stats/chain trigger/chain service/
  shape 1520/export/summary_artifacts/migration 全目录（含双头修复后的
  roundtrip）→ 全过。
- 前端：`npx vitest run` 1034 passed；`npx tsc --noEmit` clean（含测试）。
- 后端全量套件：见本 PR CI（backend-test）。

## Revisit

- `PARTIAL_SUCCESS` 存量行清零后（~3 天），可立轻量单收口 6 处终态集合字面量
  中的该值与 TRIGGERABLE 集合（仅删字面量，不动枚举）。
- 出现需要「批次成败门控」的流程（出厂判定）→ 在结果层（test_case_result）
  建判据，不回灌 run 状态（ADR-0048 §5）。
- abort→FAILED 若与「完成即绿」产生新冲突，随 #783 复审一并裁决。
