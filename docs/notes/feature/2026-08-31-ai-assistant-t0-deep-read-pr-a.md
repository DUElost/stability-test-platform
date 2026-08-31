# AI 助手 PR-A：T0 深读工具（阶段三附录）

Status: implemented
Class: feature

## 决定了什么

按 [ADR-0031 附录 A](../adr/ADR-0031-appendix-phase3-core-write-tools.md) PR-A 新增 6 个 T0 查询工具（总 T0=14）：

- `list_plans` / `get_plan_detail` — 镜像 `GET /plans` 列表与详情（含 legacy AEE Plan 隐藏）
- `preview_plan_dispatch` — 镜像 `POST /plans/{id}/run/preview`，只读预检
- `get_plan_run_jobs` — 镜像 jobs 列表
- `get_plan_run_watcher_summary` — 精简 watcher（job 分布 + link_stats + risk）
- `get_plan_run_log_events` — 镜像 DLE 列表

实现仍走 `execute_query` 直读/同源 service，不经 HTTP 自调；权限与 API 一致（登录用户可见）。

## 放弃的备选

- **watcher 全量 JSON 回填**：拒绝；字段过多占 token，v1 只回运维摘要。
- **preview 内嵌设备可用性扫描**：拒绝；与 API preview 语义一致（可用性在 `prepare_plan_run` 阶段）。

## 如何验证

- `pytest backend/tests/services/test_ai_tools.py -q`
- 助手对话：「列出 GPU 专项 Plan」→ `list_plans`；「预检 plan X 设备 1,2,3」→ `preview_plan_dispatch`

## 何时重议

- PR-B `dispatch_plan_run` 合入后，助手应能串联 list → preview → dispatch。
