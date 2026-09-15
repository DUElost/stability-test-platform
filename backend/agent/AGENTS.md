# Agent 侧 scan / upload

> 仅在改动 `backend/agent/` 时加载。**根启动契约（总原则/8 条硬不变量）住仓库根
> `AGENTS.md`**——本文件只覆盖 scoped 细节，改共享层（根规则/依赖/workflows）前
> 先读它（#857 纵深防御）。
> AEE 崩溃检测链细则见 `aee/AGENTS.md`；
> **控制面侧的 merge / SAQ 链 / 风险评级 / NFS 路径约定见
> `docs/design/2026-scan-upload-merge-contract.md`**。

- **ScanRunner** (`scan_runner.py`): calls `start_log_scan.py -m 0 -d {hdd_root} -side {side} [-end]` — AEE_TNE mode（扫 HDD，不依赖外部 DB；**不是** `-dedup_org`）。产出 HDD 上的 `Result_*_org.xls`。
- **UploadManager** (`upload_manager.py`): scan xls → NFS `dedup/{plan_run_id}/{mtk|unisoc}/{host_id}_{filename}`。事件目录由 **EventUploader** 上送到 `devices/{plan_run_id}/`；完整筛选与路径契约见 `docs/design/2026-scan-upload-merge-contract.md`。
- **reload_config 的 Agent 侧动作**（`main.py` 的 `elif command == "reload_config"`）：先 `_reload_runtime_env()` 重读安装目录 `.env` + `reset_agent_settings_caches()`，再刷新**五样**——
  1. `ScanRunner.instance().configure(force=True)`
  2. `UnisocScanRunner.instance().configure(force=True)`
  3. `UploadManager.instance().configure(force=True)`
  4. `EventUploader.instance().configure(..., force=True)`（+ `start()`）
  5. `operation_scheduler.reload_from_env()`（host 级并发上限，**容易漏**）

  另有**实例级 re-apply**（`configure()` 工厂之外的常驻对象，#2086）：`heartbeat_thread.reload_from_settings()` 与
  `coordinator.reload_from_settings()`——它们只在构造时取 Settings，漏掉这两行则
  `STP_HEARTBEAT_INTERVAL_MIN/MAX`、`STP_ADB_REPAIR_COOLDOWN_SECONDS`、
  `COORDINATOR_HEARTBEAT_INTERVAL` 的热重载**静默无效**（打印 done 但值不变）。

  下发它的 endpoint / SocketIO 命令契约见
  `docs/design/2026-scan-upload-merge-contract.md`。
