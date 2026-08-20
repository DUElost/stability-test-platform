# upload/extract 完成度写入 run_context 并前端展示

Status: implemented
Class: feature

## Decision

#300（DEVICE_LOG_FLOW_REVIEW_2026-08-09 §二 P3-2/P3-4）收口：上送与提取的
完成度不再只存在日志里，落到 `PlanRun.run_context`，前端 PlanRun 详情页可见。

- **`upload_summary`**：`merge_task` 等 DLE 上送窗口结束后（无论是否等齐）
  写入。由 `summarize_upload_states` 按 state 分组统计，`pending` =
  UPLOAD_PENDING + UPLOADING + UPLOAD_FAILED，`remote` = REMOTE + ARCHIVED +
  PRUNED；另带 `ready` 布尔（等齐判定），`LOCAL` 单独计数（过滤模型下
  「有意不传」的基线，便于判断缺口）。
- **`extract`**：`run_extract_sync` 结束时写入，记录 `targets`（远程事件路径
  条目数）、`copied`（实际新拷贝目录数）、`missing`（NFS 上缺失的路径数）、
  `existing`（目标已存在跳过数）、`merge_xls_copied`、`archived`。
- 分段写入统一走 `services/plan_run_context.write_run_context_section`，只改
  目标键，保留 `run_context` 既有内容（note / precheck / dispatch_state 等）。
- 前端在「去重报告」卡（DedupReportCard）展示两行进度（上送、提取），与
  该卡已有的 scan/merge/extract 操作同区；数据源是 PlanRun 详情接口的
  `run_context`，不新增请求。

## Alternatives

- 只写日志不落库：运维仍需翻日志/数文件，与 issue 目标相悖，未采用。
- 前端放到「存储运维概览」（ArchiveStatusCard）：该卡聚合的是 host 侧
  heartbeat 指标，upload/extract 是控制面 run_context 数据，放「去重报告」
  卡与 scan/merge/extract 操作同区更内聚，未采用。
- upload_summary 按 host 维度展开：当前 issue 验收只需看到缺口总量；host
  维度留待缺口常态化后再细化，避免首版接口过重。

## Verification

- `pytest backend/tests/services/test_dedup_extract.py`：新增
  run_context.extract 写入、upload_summary 分组统计、分段写入保留既有键
  三条测试；
- 前端 `tsc --noEmit` + build 通过；
- 实跑链路由既有 SAQ extract_task 冒烟覆盖（run_extract_sync 返回码与
  拷贝行为不变）。

## Revisit

若后续需要按 host 维度展示上送缺口，或 upload/extract 进度要进实时事件流
（SocketIO），可在现有 summary 结构上扩展，不改变写入位置。
