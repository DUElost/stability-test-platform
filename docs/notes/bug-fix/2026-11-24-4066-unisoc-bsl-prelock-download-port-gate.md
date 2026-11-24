# 展锐 BSL 刷机移除锁外 `1782:4d00` 总线闸门并补齐早退日志（v1.0.8 / #4066）

Status: implemented
Class: bug-fix

## Decision

针对 #4066 及 2026-11-23 晚 P525AA 100 台批量刷机（PlanRun 1598/1599/1615/1623，43/43 失败全部为 `Unisoc download port 1782:4d00 already present on USB bus`）的根因，发布 `flash_firmware_unisoc_bsl@1.0.8`：

1. **移除正常刷机路径锁外 `check_no_unisoc_download_port(route=route)`**：
   - 在 `backend/agent/scripts/flash_firmware_unisoc_bsl/flash_firmware_unisoc_bsl.py` 的 `run()` 中，正常刷机路径不再在获取主机排他锁 `/tmp/stp-flash-firmware.lock` 之前扫描全总线 `1782:4d00`，避免同主机 `N > STP_MAX_CONCURRENT_OPERATIONS(5)` 的后续排队设备或跨 PlanRun 重叠设备把正在合法持锁刷机的同伴端口误判为异常残留。
   - 锁内门禁（获取 `/tmp/stp-flash-firmware.lock` 之后、`adb reboot autodloader` 之前）保持不变，继续拦截真正的孤儿 `1782:4d00` 下载口；`dry_run=true` 分支保留 `check_no_unisoc_download_port` 作为离线预检。
2. **`run_spd_dump()` 提前启动输出读取线程**：
   - 将 `reader = threading.Thread(target=_read_stdout, daemon=True); reader.start()` 移至 `proc.wait(timeout=sleep_s)` 之前，并在早退（`early_rc` / `final_rc`）分支 `join` 后把 `tail[-15:]` 写入 `log` 并附带尾部摘要到错误消息，确保 `spd_dump` 启动秒退（如缺动态库）时 stderr 完整落入 `metrics.log_tail`。主线程 `final_rc = proc.poll()` 与 `adb reboot autodloader` 之间保持零 I/O 与零等待（J8 不变量不变）。
3. **涉及文件**：
   - `backend/agent/scripts/flash_firmware_unisoc_bsl/flash_firmware_unisoc_bsl.py`
   - `backend/agent/tests/test_flash_firmware_unisoc_bsl.py`
   - `backend/schemas/pipeline_templates/flash_unisoc_bsl.json`（模板 pin 追平至 `1.0.8`）
   - `tool_manifest.json`（按 ADR-0051 追加登记 `flash_firmware_unisoc_bsl@1.0.8`）

## Alternatives

- **方案 B：在锁外先非阻塞探试 `/tmp/stp-flash-firmware.lock`，仅在锁空闲时做锁外 `1782:4d00` 检查**。
  - 放弃原因：探锁与查总线之间仍有 TOCTOU 窗口（探锁瞬间同伴恰好刚拿锁进入下载态），且多出一次无收益的锁外探测；真实防护点本就在持锁之后、发送 `adb reboot autodloader` 之前的临界区内（与 MTK `flash_firmware.py` 持锁后再门控一致）。
- **方案 C：本单同时将锁内 `1782:4d00` 闸门收窄为仅拦截目标物理口（允许非目标口残留 `1782:4d00`）**。
  - 放弃原因：属于故障隔离策略语义变更，需额外确认异常坏机滞留下载口时的运维可见性与止损策略，不应混入本次 P0 竞态修复；留作后续独立议题。

## Verification

- `env -i PATH="$PATH" PYTHONPATH=. /home/debian13/stability-test-platform/venv/bin/python scripts/run_pytest.py backend/agent/tests/test_flash_firmware_unisoc_bsl.py -q`
- `env -i PATH="$PATH" PYTHONPATH=. /home/debian13/stability-test-platform/venv/bin/python scripts/run_pytest.py backend/agent/tests/ -q`
- `/home/debian13/stability-test-platform/venv/bin/python tools/dev/check_script_packages.py`
- `/home/debian13/stability-test-platform/venv/bin/python tools/dev/check_tool_manifest.py --base origin/main`
- `/home/debian13/stability-test-platform/venv/bin/python scripts/run_gates.py check:quick`

## Revisit

- 当一台设备异常卡死在 `1782:4d00` 导致同主机其余正常设备被锁内全总线闸门连坐时，基于 `spd_dump@20261119` 的 `--usb-port` 口级绑定能力复评是否将锁内闸门收窄为目标口校验 + 非目标口告警（见 #3995）。
- PR 合入后按 `script-version-lifecycle` SOP 执行 `--publish --packages-root /mnt/stp-aee/packages` 与 `POST /api/v1/scripts/scan`，并将 P525AA / P532AA 刷机计划指向 `1.0.8`。
