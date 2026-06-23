# 验收矩阵：方案 C — Sprint 2 & 3

- **PRD**：[2026-plan-c-storage-and-archive.md](../prd/2026-plan-c-storage-and-archive.md)
- **设计**：[2026-plan-c-storage-and-access.md](../design/2026-plan-c-storage-and-access.md)
- **跟踪**：[GitHub #32](https://github.com/DUElost/stability-test-platform/issues/32)
- **Agent PR**：[PR #31](https://github.com/DUElost/stability-test-platform/pull/31)

> **约定**：「自动化」列是单一事实源；本表为场景索引，步骤细节以测试代码为准。  
> **手工/E2E**：引用 [`preprod-drill-runbook.md`](../preprod-drill-runbook.md) 时只填章节号，不重复全文。

---

## Sprint 2 — Agent（PR #31）

| ID | 场景 | 前置 | 期望结果 | 自动化 | 状态 |
|----|------|------|----------|--------|------|
| AC-S2-01 | `get_aee_local_root` 默认 HDD | 无相关 env | 返回 `/mnt/hdd/aee_events` | `backend/agent/tests/test_aee_processor.py::test_get_aee_local_root_default_is_hdd` | PR #31 |
| AC-S2-02 | 默认 mobilelog/bugreport 子目录 | `stp` 布局 | 事件目录下为 `mobilelog/`、`bugreport/` | `test_aee_processor.py`、`test_aee_bugreport.py` 相关用例 | main / #29 |
| AC-S2-03 | LogArchiver prune 终态 Job | grace=0，非 ACTIVE job | SSD 上 `{job_id}/` 目录删除 | `backend/agent/tests/test_log_archiver.py` | PR #31 |
| AC-S2-04 | 跳过 ACTIVE job | job 在 LocalDB 为 ACTIVE | 目录保留 | `test_log_archiver.py::test_skip_active_job` | PR #31 |
| AC-S2-05 | grace 未到期不 prune | grace>0，新目录 | 目录保留 | `test_log_archiver.py::test_skip_not_aged` | PR #31 |
| AC-S2-06 | `archive_now` = grace=0 prune | SocketIO 触发 | 仅本地删除，无 tar/注册 | `test_log_archiver.py::test_scan_once_grace_zero_*` | PR #31 |
| AC-S2-07 | HDD spill 最旧事件 | HDD 使用率超阈 | copytree 到 CIFS `devices/` 后本地 prune | `backend/agent/tests/test_local_disk_monitor.py` | PR #31 |
| AC-S2-08 | run_log_server 列表 | job 目录存在 | `GET /run-logs/{id}` 200 + 文件列表 | `backend/agent/tests/test_run_log_server.py` | PR #31 |
| AC-S2-09 | run_log_server 下载 | 合法文件名 | `GET /run-logs/{id}/{file}` 200 + body | `test_run_log_server.py` | PR #31 |
| AC-S2-10 | path traversal 拒绝 | `../` 等 | 400/404 | `test_run_log_server.py` | PR #31 |
| AC-S2-11 | 无 cycle 快照 | patrol 周期 | 不调用 snapshot / 不写 snapshots/ | 代码审查 + pipeline 无 callback | PR #31 |
| AC-S2-12 | 不注册 run_log_bundle | Agent 完成 Job | 无 `artifact_type=run_log_bundle` POST | `agent_api` 白名单已删；`test_agent_api_artifacts` 对照 | PR #31 |
| AC-S2-13 | CI 全绿 | PR #31 | backend-test + frontend-check pass | `.github/workflows/ci.yml` | **待修** |

### Sprint 2 手工冒烟（可选）

| ID | 步骤 | 期望 | Runbook |
|----|------|------|---------|
| AC-S2-M1 | 真机 AEE crash 后查 HDD 路径 | 事件目录在 Agent `/mnt/hdd/aee_events/...` | 新建：联调记录 |
| AC-S2-M2 | `curl Agent:8900/run-logs/{job_id}` | 返回 JSON 文件列表 | DEPLOY 网络节 |

---

## Sprint 3 — 控制面 / 前端（待 follow-up PR）

| ID | 场景 | 前置 | 期望结果 | 自动化 | 状态 |
|----|------|------|----------|--------|------|
| AC-S3-01 | watcher-summary archive 新语义 | 心跳运维指标 | archive 段展示 ops_metrics（pruned/hdd%/spill）+ scan 占位 | `test_plan_run_aggregation_endpoints.py` | 本 PR |
| AC-S3-02 | risk_summary 从 log_signal 聚合 | ~~Won't fix~~ — 团队决定：控制面不代理 Agent `:8900`，运维直链 + `DEPLOY.md` | — | — | Won't fix |
| AC-S3-03 | risk_summary 非全零空态 | 有 log_signal | 初筛选数据驱动，不再从 tar 读取（原链路永不工作） | `report_service.aggregate_risk_summary_from_signals` | 本 PR |
| AC-S3-04 | archive-status 新语义 | host 有 extra | 返回 agent_metrics/capacity/health/agent_version + scan 占位 | `agent_api.get_archive_status` | 本 PR |
| AC-S3-05 | ArchiveStatusCard 运维展示 | 有 ops_metrics | 显示 HDD 使用率/SSD 清理/溢出指标，无 bundle 路径 | `WatcherSummaryCard.test.tsx` + `ArchiveStatusCard` | 本 PR |
| AC-S3-06 | dedup scan archive 条件 | log_signal 存在 | check_archive_completed 按 JobLogSignal 去重计数，不依赖 run_log_bundle | `dedup_scan.check_archive_completed` | 本 PR |
| AC-S3-07 | plan_runs 下载 run_log_bundle | GET artifact download | 409 + 方案 C 文案 | `test_download_run_log_bundle_returns_409` | PR #31 部分 |

---

## Sprint 4 — 索引（详表随 #30 增补）

| ID | 场景 | 跟踪 |
|----|------|------|
| AC-S4-01 | Agent 本地 scan 产出 xls | [#30](https://github.com/DUElost/stability-test-platform/issues/30) |
| AC-S4-02 | 终态自动 scan + merge | `aggregator` + dedup 测试 |
| AC-S4-03 | 五触发上送 | `plan_runs` + Plan `auto_archive_interval_seconds` |
| AC-S4-04 | dedup 完成条件不依赖 run_log_bundle | Sprint 3 已改写 `check_archive_completed`（按 log_signal 去重） | `dedup_scan` |

---

## 发版勾选（最小集）

上线方案 C Agent（Sprint 2）前至少通过：

- [ ] AC-S2-01 ~ AC-S2-12 自动化绿
- [ ] AC-S2-13 CI 绿
- [ ] AC-S2-M1 或等价真机签字（团队约定）
- [ ] Sprint 3 未做前：**不对外承诺** Archive 区 / risk_summary / dedup 全自动正确（见 PR #31 审查）

---

## 修订记录

| 日期 | 变更 |
|------|------|
| 2026-06-21 | 初版 Sprint 2/3 矩阵 |
