# 验收矩阵：方案 C — Sprint 4

- **PRD**：[2026-plan-c-storage-and-archive.md](../prd/2026-plan-c-storage-and-archive.md)
- **设计**：[2026-plan-c-storage-and-access.md](../design/2026-plan-c-storage-and-access.md)
- **实施计划**：[2026-06-22-sprint4-scan-upload-merge.md](../archive/sprints/plans/2026-06-22-sprint4-scan-upload-merge.md)
- **PR**：[PR #35](https://github.com/DUElost/stability-test-platform/pull/35)

> 「自动化」列是单一事实源；本表为场景索引，步骤细节以测试代码为准。

---

## Sprint 4 — Agent 本地 scan + 按需上送 + 控制面 merge

| ID | 场景 | 前置 | 期望结果 | 自动化 | 状态 |
|----|------|------|----------|--------|------|
| AC-S4-01 | ScanRunner `-dedup_org` | scan tool 已部署 | subprocess 调用 `start_log_scan.py -dedup_org {hdd_root} -side {side}` | `backend/agent/tests/test_scan_runner.py` | PR #35 |
| AC-S4-02 | ScanRunner glob 取 fresh xls | 多个 `_org.xls` 存在 | 仅取 `mtime >= scan_start - 1` 的 fresh 文件；无 fresh 返回 None | `test_scan_runner.py::test_run_local_scan_returns_none_when_no_fresh_xls` | PR #35 + P1-5 |
| AC-S4-03 | ScanRunner timeout | subprocess 超过 600s | `subprocess.TimeoutExpired` + log.warning | `test_scan_runner.py::test_run_local_scan_timeout` | PR #35 |
| AC-S4-04 | ScanRunner configure 保护 | 已 configured 时再次调用 | log.warning + return，不重配 | `test_scan_runner.py::test_configure_rejected_if_already_configured` | PR #35 |
| AC-S4-05 | UploadManager scan 报告上送 | `_org.xls` 存在 | copy 到 `NFS/dedup/{plan_run_id}/{host_id}_{filename}` | `backend/agent/tests/test_upload_manager.py` | PR #35 |
| AC-S4-06 | UploadManager 事件目录上送 | 事件目录存在 | copytree 到 `NFS/devices/{plan_run_id}/{dirname}/` | `test_upload_manager.py` | PR #35 |
| AC-S4-07 | UploadManager dest 已存在跳过 | 目标已存在 | 跳过 + log.info | `test_upload_manager.py` | PR #35 |
| AC-S4-08 | UploadManager CIFS EPERM 处理 | copytree 权限错 | 忽略 shutil.SameFileError + log.debug | `test_upload_manager.py::test_copytree_safe_eperm` | PR #35 |
| AC-S4-09 | Agent `scan_now` 命令 | SocketIO control | daemon 线程 → ScanRunner → UploadManager | `backend/agent/main.py:614-647` | PR #35 |
| AC-S4-10 | Agent `upload_events` 命令 | SocketIO control | daemon 线程 → UploadManager.upload_event_dirs | `backend/agent/main.py:649-670` | PR #35 |
| AC-S4-11 | SAQ scan_task 发 SocketIO | 终态触发 | emit `scan_now` 到各 ONLINE host | `backend/tasks/saq_tasks.py` | PR #35 |
| AC-S4-12 | SAQ upload_task | SAQ 入队 | emit `upload_events` 到各 ONLINE host | `saq_tasks.py` | PR #35 |
| AC-S4-13 | SAQ merge_task | scan_task 完成后串行入队 | `run_merge_sync` 合并 NFS `dedup/{run_id}/` 下 `_org.xls` → `PlanRunArtifact(merge_result_xls)` | `saq_tasks.py` + `dedup_scan.py:run_merge_sync` | PR #35 |
| AC-S4-14 | dedup_scan `build_scan_argv` 已删 | — | 不再从控制面构建 argv | 代码删除 | PR #35 |
| AC-S4-15 | `run_scan_sync` 改 NFS 扫描 | scan 报告已在 NFS | `_register_scan_artifacts_from_nfs` | `backend/services/dedup_scan.py` | PR #35 |
| AC-S4-16 | `trigger_scan` 异步触发 | API 调用 | 查 hosts → emit `scan_now` | `backend/api/routes/dedup.py:175-222` | PR #35 |
| AC-S4-17 | `extract` 从 15.4 devices/ | merge 已有 | copytree `devices/{run_id}/` → `jira/{run_id}/` | `dedup.py:287-340` | PR #35 |
| AC-S4-18 | 五触发场景①—终态自动 | PlanRun 终态 | `enqueue_dedup_terminal`: scan→upload→merge | `aggregator.py` + `dedup_scan.py` | PR #35 |
| AC-S4-19 | 五触发场景②—abort/失败确认 | abort 流程 | `aggregator_sync` + `plan_run_abort` 已覆盖 | 现有测试 | PR #35 |
| AC-S4-20 | 五触发场景③—原三场景 | 保留 | 保留不变 | — | N/A |
| AC-S4-21 | 五触发场景④—手动归档 | POST archive | 同时发 `archive_now` + `scan_now` | `test_plan_run_archive_endpoint.py` | PR #35 |
| AC-S4-22 | 五触发场景⑤—auto_archive | PlanRun 终态 + interval 过期 | `auto_archive_sweep` enqueue scan→upload→merge | `cron_scheduler.py:auto_archive_sweep` | PR #35 |
| AC-S4-23 | `Plan.auto_archive_interval_seconds` 列 | — | nullable INTEGER, CRD API 暴露 | `models/plan.py` + 迁移 | PR #35 |
| AC-S4-24 | `scan_status` 从 artifact 计算 | 无/scan/merge artifact | pending / scanned / merged | `plan_runs.py:_aggregate_run_log_archive` | PR #35 |
| AC-S4-25 | `scan_triggered_at` 取最早 artifact | 有 scan artifact | 最早 `created_at.isoformat()` | `_aggregate_run_log_archive` | PR #35 |
| AC-S4-26 | 前端 DedupReportCard | PlanRun 详情页 | scan/merge/extract 按钮 + 产物列表 + artifact_type 标注 | `DedupReportCard.tsx` + `WatcherSummaryCard.tsx` | PR #35 + #17 |
| AC-S4-27 | 前端 DedupScanStatus 类型 | — | pending/scanned/merged | `types.ts:DedupScanStatus` | PR #35 |
| AC-S4-28 | Agent standalone import | `PYTHONPATH=backend` | `import agent.main` 不依赖 `backend.` 包 | `test_legacy_tool_cleanup.py` | PR #35 |
| AC-S4-29 | APScheduler auto_archive_sweep | 已注册 | 每 120s 执行，interval 可配 | `app_scheduler.py` | PR #35 |

---

## 发版勾选（最小集）

上线方案 C Sprint 4 Agent 功能前至少通过：

- [ ] AC-S4-01 ~ AC-S4-10 Agent 单元测试 584 PASS
- [ ] AC-S4-11 ~ AC-S4-17 控制面逻辑（现有 + 改写）
- [ ] AC-S4-18 ~ AC-S4-22 五触发场景自动化绿
- [ ] AC-S4-23 ~ AC-S4-25 scan_status 计算正确
- [ ] AC-S4-26 ~ AC-S4-27 前端 build 通过
- [ ] AC-S4-28 standalone import test PASS
- [ ] AC-S4-29 scheduler job 注册正常
- [ ] 真机联调：Agent scan 产出 _org.xls + UploadManager 上送 NFS

---

## 修订记录

| 日期 | 变更 |
|------|------|
| 2026-06-23 | 初版 Sprint 4 验收矩阵 |
