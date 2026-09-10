# 报告缓存语义裁决：快照 + PlanRun 终态刷新（#1082）

Status: implemented
Class: bug-fix

## Decision

#1082（R10-F14，设计风险）：单 Job 完成时 `post_completion` 缓存的是**当时整个
PlanRun** 的报告（`job.report_json`）；其他 Job 或迟到事件随后变化时缓存不更新，
而 `/runs/{id}/report`（live）始终重算 —— 两个口径不一致且无标注。issue 要求
「产品裁决语义后统一刷新策略或 UI 标注」。

**裁决（用户 2026-09-09）：快照 + 终态刷新**：

- 单 Job 完成时刻的缓存**保持快照语义**（post_completion 原行为不变）——报告的
  天然锚点是「本 Job 完成」，此时 PlanRun 尚未静止，实时重算也不会更"正确"；
- **PlanRun 终态**（`_finalize_plan_run`，两条聚合路径的唯一收口）时数据才真正
  静止，此时调度 `refresh_report_cache_for_plan_run`（独立 SessionLocal、线程池
  best-effort）批量重算该 run 下所有已后处理 job 的 `report_json`——**此后快照
  即最新最终结果**，两个口径收敛；
- 快照生成时刻暴露：`/runs/{id}/report/cached` 缓存命中时响应体附
  `cached_at`（= `post_processed_at`，语义 = 报告生成时刻，终态刷新后即最终结果
  的生成时刻），UI 据此标注「截至 xx 时刻」；需要实时口径走 `/runs/{id}/report`。

实现细节：

- `_schedule_report_cache_refresh(plan_run_id)`：**TESTING=1 时跳过** —— 后台
  线程与「按用例 TRUNCATE」的测试隔离模型天然竞争（重算线程可能在下一用例的
  数据上跑），会污染无关用例；验证逻辑的用例直接调用 refresh 函数。
- 刷新只针对 `post_processed_at IS NOT NULL` 的 job（曾生成过报告的）；逐条
  try/except，单份失败不影响其余。
- 调度是 best-effort：线程池满（`PoolQueueFullError`，#1122）等异常放弃本轮，
  cached 端点的 live 兜底仍给出正确数据。

## Alternatives

- 纯实时语义（cached 永远 live 重算）：放弃 post_completion「报告可即刻服务」
  的初衷，且每次页面访问重算 —— 用户裁决未采纳；
- 纯 UI 标注不刷新：快照与实时口径的差异永久存在，「裁决收敛」未达成；
- 刷新挂在各 Job 的终态（而非 PlanRun 终态）：等于每个 Job 完成都全量重算一次
  其他 job 的报告，N² 成本；终态一次刷新是成本与语义的平衡点。

## Verification

- `pytest backend/tests/services/test_post_completion.py`：11 passed，新增 4 例
  ——refresh 重算全部已后处理 job 且跳过未后处理的（`post_processed_at` 更新、
  旧缓存键被替换）/ cached 端点带 `cached_at` / 调度门控（TESTING 跳过、非测试
  环境经线程池提交且参数正确）/ `_finalize_plan_run` 收口处调度必达；
- `test_runs.py` + `test_plan_run_aggregation_shared.py`：既有 cached/live 与聚合
  用例不受影响；
- `pytest backend/tests/services`：全目录通过；ruff 干净。

## Revisit

- UI 标注：前端拿到 `cached_at` 后在报告页显示「截至 xx」——前端改动不在本单
  （后端契约已就绪），需要时另开 UI 单；
- 终态刷新重算 N 份报告（N = run 下已后处理 job 数）：大批量 run（1000 设备）
  的刷新耗时待实测；如成为负担，可改为「终态只刷新 summary 维度、详情按需」；
- 若终态刷新与 Agent 迟到上送竞争（DLE 在终态后才落库），下一轮手动重跑
  （post_completion 重入）仍会覆盖 —— 语义上「最终结果」以最后一次刷新为准。
