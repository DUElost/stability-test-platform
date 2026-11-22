# B10 U2：Agent L1 固件本地缓存基座、定时清理与双路径能力门控

Status: implemented
Class: feature

关联：[#4086](https://github.com/DUElost/stability-test-platform/issues/4086)（批次方案 v1.5）、
[#4076](https://github.com/DUElost/stability-test-platform/issues/4076)、
[#3804](https://github.com/DUElost/stability-test-platform/issues/3804)、
[ADR-0064](../../adr/ADR-0064-host-firmware-cache-and-transfer-pacing.md) D2（Accepted v1.0）。

## Decision

按批次方案 [#4086](https://github.com/DUElost/stability-test-platform/issues/4086) §3 U2 与
[ADR-0064](../../adr/ADR-0064-host-firmware-cache-and-transfer-pacing.md) D1–D3（Accepted v1.0）落地：

1. 新增 `backend/agent/firmware_cache.py`：`FIRMWARE_CACHE_API_VERSION = 1`、
   `ensure_firmware` 上下文管理器；复用 U1 `firmware_manifest` 与 U3
   `TransferPermitClient`；一级/异盘溢出、空间守卫、`.entry.lock` /
   `.host-transfer.lock` 固定偏序、降级复核、TTL 清理与 `insufficient_local_cache_space`
   fail-closed。
2. `FirmwareCacheSettings` 收敛 ADR-0064 D2 #3 全部 9 个 `STP_FIRMWARE_*` 旋钮，并接入
   `reset_agent_settings_caches` / env 清单。
3. `pipeline_engine` 双路径 `PYTHONPATH`；`firmware_cache_v1` 能力常量；
   `requires_agent_capabilities` 在 `verify_package` 与 `inject_required_tools` fail-closed。

## Alternatives

- 在刷机脚本内各自维护缓存：违背 ADR-0064 D2「归属 L1」与 #3498，否决。
- 命中时重读源盘算 SHA：正是 F2 缺陷形态，否决；已封包命中只校本地 ready + 本地完整性。
- 清理线程停机挂入 `graceful_shutdown.py`：该文件不在 U2 declare scope；采用 daemon
  线程 + `FirmwareCacheManager.stop()`（测试/可选），进程退出由内核收口。记入 PR「观察」。

## Verification

- `./scripts/project_python.sh scripts/run_pytest.py backend/agent/tests/test_firmware_cache_b10_u2.py backend/agent/tests/test_capabilities_4044.py backend/tests/services/test_shadow_scan_capability_4044.py backend/agent/tests/test_bootstrap_subsystems_736.py tests/test_inner_import_ratchet.py -q`
- `./scripts/project_python.sh tools/dev/check_governance_surface.py --base origin/main --check`
- `./scripts/project_python.sh scripts/run_gates.py check:quick`
- `./scripts/project_python.sh tools/dev/env_inventory.py --write`（随后 `--check` 经 check:quick）

## Revisit

- U4 三大刷机族接入 `ensure_firmware` 与 `requires_agent_capabilities` 声明。
- 若运维需要 Agent 优雅停机时显式 join 清理线程，再把 `stop()` 挂入
  `graceful_shutdown`（另开单元扩 scope）。
