# 展锐 BSL 刷机主机锁交接等待 `1782:4d00` sysfs 尾影清零（v1.0.9 / #3995）

Status: implemented
Class: bug-fix

## Decision

在 2026-11-24 P532AA 49 台批量刷机实测（PlanRun 1656，单主机 15 台并发）中，14/15 成功、1 台（`job=209896`）在获取 `/tmp/stp-flash-firmware.lock` 后毫秒级报错：

`Unisoc download port 1782:4d00 already present on USB bus (ports=['1-5.4.4'], expected none)`

经时间戳精确对账：`1-5.4.4` 是前一台 `job=209892` 刚执行完 `spd_dump ... reset` 退出后立即释放主机锁的设备；手机收到 `reset` 后 USB `1782:4d00` 从 Linux sysfs 摘除存在百毫秒至秒级延迟，紧接抢锁的下一台执行锁内单次快照扫描撞上该瞬态尾影（4ms 后进入的后续设备及之后 10 台全部正常通过）。

在 `flash_firmware_unisoc_bsl@1.0.9` 中加双保险消除该锁交接窗口竞态：

1. **释放主机锁前等待自身 `1782:4d00` 摘除**（`_wait_download_port_clear`，上限 `_POST_FLASH_PORT_CLEAR_WAIT_SECONDS=10.0s`）：`run_spd_dump` 正常返回后、`finally` 释放 `/tmp/stp-flash-firmware.lock` 之前，轮询确认自身 `usb_port`（未绑口时为全总线）不再枚举 `1782:4d00`；超时仅记录日志不把已刷完设备误标失败。
2. **锁内 `check_no_unisoc_download_port` 增加短宽限轮询**（`wait_seconds`，默认 `_DEFAULT_DOWNLOAD_PORT_CLEAR_WAIT_SECONDS=5.0s`，可由步骤参数 `download_port_clear_wait_seconds` 覆盖；`dry_run=true` 保持 `0.0s`）：首次扫描无 `1782:4d00` 立即返回（零延迟）；若存在瞬态尾影则在宽限窗内轮询复查，消失即放行，持续存在仍按 fail-closed 抛 `FlashRejectedError` 拦截真正孤儿口。
3. **涉及文件**：
   - `backend/agent/scripts/flash_firmware_unisoc_bsl/flash_firmware_unisoc_bsl.py`
   - `backend/agent/tests/test_flash_firmware_unisoc_bsl.py`
   - `backend/schemas/pipeline_templates/flash_unisoc_bsl.json`（pin 追平至 `1.0.9`）
   - `tool_manifest.json`（按 ADR-0051 追加登记 `flash_firmware_unisoc_bsl@1.0.9`）

## Alternatives

- **仅在释放锁前固定 `time.sleep(3)`**：弃——正常设备 0.2s 即可断开却白白浪费 3s，而慢设备若需 4s 仍会偶发撞尾影；按 sysfs 实测轮询兼顾零额外开销与准确覆盖。
- **直接删除锁内 `1782:4d00` 门禁**：弃——无法拦截未绑口模式或前任异常滞留孤儿下载口；保留锁内门禁并加短宽限轮询既防真孤儿又消瞬态尾影。

## Verification

- `env -i PATH="$PATH" PYTHONPATH=. /home/debian13/stability-test-platform/venv/bin/python scripts/run_pytest.py backend/agent/tests/test_flash_firmware_unisoc_bsl.py -q`
- `/home/debian13/stability-test-platform/venv/bin/python scripts/run_pytest.py tests/test_pipeline_template_script_pins_2865.py -q`
- `/home/debian13/stability-test-platform/venv/bin/python tools/dev/check_script_packages.py`
- `/home/debian13/stability-test-platform/venv/bin/python tools/dev/check_tool_manifest.py --base origin/main`

## Revisit

- 若后续引入单主机多设备并行刷机或单台真孤儿口不连坐策略，可基于 `spd_dump@20261119` 的 `--usb-port` 口级硬绑定能力复评锁内门禁范围（见 #3995）。
