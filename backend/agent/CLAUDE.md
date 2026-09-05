# Agent 侧 scan / upload

> 仅在改动 `backend/agent/` 时加载。
> AEE 崩溃检测链细则见 `aee/CLAUDE.md`；
> **控制面侧的 merge / SAQ 链 / 风险评级 / NFS 路径约定见
> `docs/design/2026-scan-upload-merge-contract.md`**。

- **ScanRunner** (`scan_runner.py`): calls `start_log_scan.py -m 0 -d {hdd_root} -side {side} [-end]` — AEE_TNE mode（扫 HDD，不依赖外部 DB；**不是** `-dedup_org`）。产出 HDD 上的 `Result_*_org.xls`。
- **UploadManager** (`upload_manager.py`): scan xls → NFS `dedup/{plan_run_id}/{mtk|unisoc}/{host_id}_{filename}`。事件目录由 **EventUploader** 上送到 `devices/{plan_run_id}/`；完整筛选与路径契约见 `docs/design/2026-scan-upload-merge-contract.md`。
- **reload_config 的 Agent 侧动作**（`main.py` 的 `elif command == "reload_config"`）：先 `_reload_runtime_env()` 重读安装目录 `.env`，再刷新**三样**——
  1. `ScanRunner.instance().configure(force=True)`
  2. `UploadManager.instance().configure(force=True)`
  3. `operation_scheduler.reload_from_env()`（host 级并发上限，**容易漏**）

  下发它的 endpoint / SocketIO 命令契约见
  `docs/design/2026-scan-upload-merge-contract.md`。
