# 展锐 BSL 刷机锁内覆盖冷启动稳定窗并按口级绑定收窄 `1782:4d00` 门禁（v1.0.10 / #3995）

Status: implemented
Class: bug-fix

## Decision

在 2026-11-25 P525AAN 批量刷机复盘（PlanRun `#1679`，53 台并发派发）中定位到两项脚本侧根因：

1. **放锁过早导致邻机冷启动重枚举撞上后一台写分区**：`flash_firmware_unisoc_bsl@1.0.9` 在 `spd_dump reset` 返回并等待 10s 下载口消失后即释放 `/tmp/stp-flash-firmware.lock`，随后 `wait_boot_completed` / `verify` 在锁外执行。内核 USB 日志证实 `15.42`（`job=210294`，`1-4.2.4`）与 `15.41`（`job=210293`，`1-5.1.3`）均在前一台放锁 7–8s 后进入 `1782:4d00` 写分区，并在前一台冷启动二次 USB 重枚举（`+19s` / `+45s`）期间发生 `send: type = 0x02, size = 32768 | timeout reached`，导致设备卡死在下载态。
2. **全总线 `1782:4d00` 门禁在已启用 `--usb-port` 口级绑定时仍连坐整机**：`15.42` 与 `15.41` 各有 1 台卡死在 `1782:4d00` 后，同主机其余 22 台排队正常设备在 100 秒内全部被锁内全总线扫描 `Unisoc download port 1782:4d00 already present on USB bus` 秒杀（延续 `#3995` 已登记的复议项）。

在 `flash_firmware_unisoc_bsl@1.0.10` 中同步落地两项加固：

1. **锁内覆盖刷后冷启动稳定窗**：
   - 新增步骤参数 `hold_lock_until_boot`（默认 `True`）与 `boot_stabilize_seconds`（默认 `5.0`，必须为有限非负数；`0` 关闭额外静置）。
   - `hold_lock_until_boot=True` 时，主机锁 `/tmp/stp-flash-firmware.lock` 保持持有至 `wait_boot_completed` 返回 `sys.boot_completed=1` 且完成 `boot_stabilize_seconds` 静置（期间打 `stage="boot-stabilize"` PROGRESS 戳）后再于 `finally` 释放，避免前一台冷启动二次 USB 重枚举干扰后一台烧写。
   - `wait_boot_completed` 切至单调时钟 `time.monotonic()` 并按剩余预算约束单次 `run_adb` 超时与轮询间隔，避免系统墙钟回拨/跳变拉长或提前截断锁内开机等待。
   - 主机锁交接（`acquire_host_lock` 异常路径与 `run()` `finally`）通过 `_release_host_lock` 独立执行 `LOCK_UN` 与 `os.close(fd)`，保证 `lock-held` 打戳异常或 `LOCK_UN` 返回 `OSError` 时仍可靠关闭文件描述符释放锁。
   - `metrics` 新增 `lock_held_until_boot` 与 `boot_stabilize_seconds` 供回溯。
2. **口级绑定模式下收窄 `1782:4d00` 门禁为仅拦截目标物理口**：
   - `check_no_unisoc_download_port` 新增 `target_usb_port`、`log` 与 `progress` 参数，返回被忽略的非目标口列表 `List[str]`；当存在被忽略的非目标口孤儿时，同步通过 `stderr` PROGRESS（`stage="download-port-warning"`）与成功路径 `metrics.warnings` 输出可采集告警。
   - `_sysfs_read` 在 `find_unisoc_download_ports` 读取 `idVendor` / `idProduct` 时区分节点瞬态消失（`ENOENT` / `ENODEV`）与属性不可读故障（如 `EACCES` / `EIO`），属性不可读一律 fail-closed 拒绝，防止门禁被权限或 I/O 异常绕过。
   - 当 `require_port_binding=True` 且已解析唯一 `usb_port`（配合 `spd_dump@20261119` 的 `--usb-port <port>` 与退出后 `Opened 1782:4d00 on usb-port <port>` 回显校验）时：仅当 `usb_port` 自身仍在 `1782:4d00` 时在宽限窗内轮询并 fail-closed；若仅存在非目标口孤儿 `1782:4d00`，记录告警并写入 `metrics.non_target_download_ports_ignored` 与 `metrics.warnings`，不再连坐同主机正常设备。
   - 当 `require_port_binding=False`（即使仍能解析出信息性 `usb_port`）或 `sysfs` 目录/属性不可读时，门禁仍严格维持全总线 fail-closed。
   - `dry_run=True` 与运行期均保证门禁与最终 `usb_port` 使用同一次有效校验的绑定：绑口模式下 `dry_run` 先解析 `usb_port`（若串号缺失仍先走全总线门禁报 `1782:4d00`），通过门禁后复验绑定未漂移；运行期在锁内完成 `boot_id` 读取与下载口门禁后、启动 `spd_dump` 前再次复验目标口未漂移，杜绝过时 USB 口进入刷写器参数。
3. **涉及文件**：
   - `backend/agent/scripts/flash_firmware_unisoc_bsl/flash_firmware_unisoc_bsl.py`
   - `backend/agent/tests/test_flash_firmware_unisoc_bsl.py`
   - `backend/schemas/pipeline_templates/flash_unisoc_bsl.json`（pin 追平至 `1.0.10`）
   - `tool_manifest.json`（按 ADR-0051 追加登记 `flash_firmware_unisoc_bsl@1.0.10`）

## Alternatives

- **仅在放锁前固定 `time.sleep(60)` 而不等 `sys.boot_completed=1`**：弃——不同机型与首次开机耗时差异大，固定等待既可能白白浪费时间也可能覆盖不全；直接把已有的 `wait_boot_completed` 纳入锁内再附 5s 静置，兼顾确定性与最小延迟，且提供 `hold_lock_until_boot=false` 作为回退开关。
- **直接删除 `1782:4d00` 门禁**：弃——未绑口模式下 `spd_dump` 会误连任意下载口，目标口本身已在 `1782:4d00` 时也无法走 `adb reboot autodloader` 正常流程；按 `target_usb_port` 分层既保住安全底线又消除整机连坐。

## Verification

- `env -i PATH="$PATH" PYTHONPATH=. /home/debian13/stability-test-platform/venv/bin/python scripts/run_pytest.py backend/agent/tests/test_flash_firmware_unisoc_bsl.py -q`
- `/home/debian13/stability-test-platform/venv/bin/python scripts/run_pytest.py tests/test_pipeline_template_script_pins_2865.py -q`
- `/home/debian13/stability-test-platform/venv/bin/python tools/dev/check_script_packages.py`
- `/home/debian13/stability-test-platform/venv/bin/python tools/dev/check_tool_manifest.py --base origin/main`
- `/home/debian13/stability-test-platform/venv/bin/python scripts/run_gates.py check:quick`

## Revisit

- 当 `ADR-0065`（优雅中止临界区有界保护）裁决通过并落地后，本脚本将在下一版本接入 `critical_section_guard` 能力声明与 `critical_section="enter"/"exit"` PROGRESS 标记。
- 若后续观测到某机型在 `sys.boot_completed=1` 之后超过 5s 仍存在 USB gadget 重启，可通过步骤参数 `boot_stabilize_seconds` 调大并在实测后更新缺省值。
