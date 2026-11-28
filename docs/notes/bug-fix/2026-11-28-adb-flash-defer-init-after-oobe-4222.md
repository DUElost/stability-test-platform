# ADB 刷机推迟 `init_return_check` 纳入紧随其后的 `oobe_skip`（#4222）

Status: implemented
Class: bug-fix

## Decision

在 PlanRun #1770 单机冒烟（`MLD-NX3-Claro-16-261121V116` 样机）中，`flash_firmware` 成功刷写并开机（`boot_completed=1`）且 `return_check.power-cycle` 通过后，设备在进入 `return_check.gpu` 时主动关机进入 `0e8d:2046` 充电态；实机回读确认关机原因为 `sys.boot.reason.last = shutdown,provisioning_shutdown_req`（`user_setup_complete=0`、`device_provisioned=0`），即刷后未第一时间执行 `oobe_skip` 跳过 SetupWizard 触发了系统 Provisioning 自动关机。

为此在 `backend/agent/contracts/vbus_flash.py` 扩展 `deferred_flash_init_prefix_len` 与 `is_deferred_flash_step`：
- 当 `lifecycle.init` 命中刷机前缀（`[script:flash_firmware, ...]` 或 `[script:flash_preflight, script:flash_firmware, ...]`）且紧随 `flash_firmware` 之后的步骤为 `script:oobe_skip` 时，将该 `oobe_skip` 一并纳入推迟前缀（前缀长度分别为 2 或 3），先跳过 OOBE 再执行 `_check_init_returns()`；
- 单独的 `oobe_skip`（前面没有 `flash_firmware`）不推迟 `init_return_check`；
- `is_vbus_flash_init` / `is_vbus_flash_lifecycle` 控制面准入契约保持不变；
- `oobe_skip` 不建立任何专项足迹，刷后先跳 OOBE 再做残留检查与 ADR-0063 D3 第 6 条（`vbus_cycle` 在 `flash_firmware` 锁内先跳 OOBE 再出锁做 `init_return_check`）完全对齐。

## Alternatives

- **在 `flash_firmware`（`adb_reboot` 模式）内嵌 OOBE 跳过**：会改动 `flash_firmware` 脚本版本并重复 `oobe_skip` 逻辑，且与现有 ADB 刷机 Plan（显式编排 `oobe_skip` 步骤）重复执行；扩展引擎前缀零脚本发版、直接复用现有 Plan 编排。
- **在 Plan 中移除 `oobe_skip` 改为 `flash_firmware` 后不跳过 OOBE**：OOBE 会继续触发 `provisioning_shutdown_req` 关机，不可行。

## Verification

- 通过 `scripts/run_pytest.py` 运行 `backend/agent/tests/test_vbus_init_check_order_3892.py` 与 `backend/agent/tests/test_return_init_u4_3729.py`，以及后端与治理静态门禁（具体结果见 PR 记录）。

## Revisit

- 合入并部署后，使用现有 Plan `#262`/`#263`/`#264`（已编排 `precheck → flash → oobe → root → check`）对单台 V108 样机复跑冒烟，确认全链路 `SUCCESS` 且不再触发 `provisioning_shutdown_req` 后即可批量放量。
