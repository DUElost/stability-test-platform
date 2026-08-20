# Agent 启动窗口收口：scan side env、命令不丢、scan/upload 移出 watcher 门控

- **状态**：已实施
- **类别**：bug-fix
- **日期**：2026-08-20
- **关联**：#294（P1-2）、#296（P2-2a/2b）、#297（P2-3）

## 决定了什么

1. **P1-2**：`ScanRunner.configure` 默认 `side` 改读 `STP_DEDUP_SCAN_TAG`
   （含 `factory` 大小写不敏感 → `factory`，否则 `shanghai`），与控制面
   `run_merge_sync` 同逻辑；显式传 `side=` 时优先（测试/特殊部署不受影响）。
2. **P2-2a**：`main.py` 在 `sio_client.connect()` 前先注册转发 handler，
   启动窗口内到达的 control 命令入队，真实 `_handle_control` 就绪后统一回放。
3. **P2-2b**：`ScanRunner` 队列 worker 在未 configure 时把任务 requeue 等待
   （2s 间隔重试），不再走 `control_scan_now_skip_runner_not_configured` 丢弃。
4. **P2-3**：`LogArchiver` / `ScanRunner` / `UploadManager` / `EventUploader` /
   `LocalDiskMonitor` 的 configure/start 移出 `watcher_subsystem_enabled()` 门控，
   各自按自身 env 判断（EventUploader.start 与 LocalDiskMonitor 内部均有 enabled
   门禁）；watcher 专属的 LogWatcherManager / OutboxDrainer / ArtifactUploader /
   reconcile 保持门控内。

## 放弃的备选

- 只修 P2-2a 不动队列：窗口内命令仍会因 ScanRunner 未 configure 被丢弃，
  两个窗口必须一起收。
- 把五个子系统 configure 无条件放到最前：保持现有顺序（归档 → scan → upload →
  EventUploader → spill），避免改变既有启动时序。

## 如何验证

- `backend/agent/tests/` 全量 1077 passed；新增用例覆盖 side env 解析（含
  大小写、显式优先）与队列 defer（未 configure 不丢、configure 后执行）。
- `import backend.agent.main` 通过；ruff 零告警。

## 何时重议

- 若 Agent 启动顺序后续有硬依赖（如 EventUploader 必须在某子系统后启动），
  需重新评估本批的固定顺序。
