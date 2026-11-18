# Agent Note: `spd_dump@20261118` `--usb-port` 口级绑定与 `flash_firmware_unisoc_bsl@1.0.5` 接入

Status: implemented
Class: feature

## Decision

针对 `#3995 §5 第 3 项`（[B9-U2 Note](../bug-fix/2026-11-16-b9-u2-unisoc-bsl-safety-gates.md) 记录的 `spd_dump` 缺乏 USB 口级绑定），补齐工具端与脚本端端到端物理端口绑定：

1. **工具包 `spd_dump@20261118`（append-only 登记于 `tool_manifest.json`）**：
   - 在本机 `spreadtrum_flash/spd_dump.c`（`USE_LIBUSB=1`）新增 `--usb-port <bus>-<p1>[.<p2>...]` 参数（如 `1-5.2.1`）：解析期严格校验端口路径合法性，打开期通过 `libusb_get_device_list()` + `libusb_get_bus_number()` + `libusb_get_port_numbers()` 仅打开匹配 `1782:4d00` 且拓扑路径完全一致的设备，成功打开后向 `stderr` 输出 `Opened 1782:4d00 on usb-port <port>`，多设备命中或 `LIBUSB_ERROR_ACCESS` 均立即失败；未传 `--usb-port` 时保持 `20261113` 行为。
   - 确定性归档 `spd_dump-20261118.tar.gz`（`sha256: 8339f9e984142e7f654b06ebe45a76d7bbc2a8c7f7a954ab846a6cd31bf839d8`）。
2. **脚本族 `flash_firmware_unisoc_bsl@1.0.5` 与模板追平**：
   - `capabilities.json` 的 `requires_tools.spd_dump.version` 升为 `"20261118"`；
   - 新增 `require_port_binding`（缺省 `True`，支持步骤参数或 `STP_FLASH_REQUIRE_PORT_BINDING`）：Linux 下从 `/sys/bus/usb/devices` 按 `STP_DEVICE_SERIAL` 解析唯一物理端口（缺失、多匹配或非法格式在取锁前即 `FlashRejectedError` 带路由拒绝），持有主机锁准备本地缓存后复核端口未漂移，拼装 `--usb-port <port>` 启动 `spd_dump`，并在退出 `0` 时核对 `Opened 1782:4d00 on usb-port <port>` 回显；
   - `backend/schemas/pipeline_templates/flash_unisoc_bsl.json` pin 同步升至 `"1.0.5"`。

## Alternatives

- **使用 `spd_dump` 的 `LIBUSB=0` + `--tty` 串口模式**：否决。`LIBUSB=0` 缺少智能手机需要的 `libusb_control_transfer(0x21, 34, 0x601, ...)`，且额外依赖 `option`/`usbserial` 动态绑口与内核串口编号稳定。
- **不改 `spd_dump`、仅靠启动前扫描总线无 `1782:4d00`**：否决作为长期方案。无法防止等待窗内同主机并发设备意外掉入 `autodloader` 时被 `spd_dump` 抢连误刷。
- **同步在本次落地关机 `vbus_cycle` 展锐刷机**：未采纳（2026-11-18 主机 `.82` 实机否决纯 VBUS 切电前提，见 Revisit）。

## Verification

- **实机验证（2026-11-18，主机 `.82`，RTS5411 Hub）**：
  - 负向隔离（525 `<device-525>` @ `1-5.2.1`）：`1-5.2.1` 上电枚举 `1782:4d00` 时，`--usb-port 1-5.2.2` 未触碰该口并超时退出（`rc=1`）；
  - 正向握手（532 `<device-532>` @ `1-5.2.3`）：`adb reboot autodloader` 配合 `--usb-port 1-5.2.3` 精准打开目标口（`Opened 1782:4d00 on usb-port 1-5.2.3`），成功分离串口驱动、握手 `SPRD4:AutoD`、加载 `FDL1` + `edk2-fdl2-sign.bin` 并 `reset` 回到 ADB（`rc=0`）；
  - 关机 `vbus_cycle` 实测边界：525（`1-5.2.1`）与 532（`1-5.2.3`）在 `adb reboot -p` 关机后经 `usb-power off`（12s）→ `on` 均仅枚举约 2 秒 full-speed `1782:4d00` 并对 `0x7e` 零响应，随后跳转正常开机/充电（且带电池时 `usb-power off` 不断电）。
- **单测与门禁**：
  - `backend/agent/tests/test_flash_firmware_unisoc_bsl.py`：**81 passed**（74 条既往回归 + 7 条 `1.0.5` 口级绑定用例）；
  - `tests/test_pipeline_template_script_pins_2865.py`：**7 passed**；
  - `tools/dev/check_script_packages.py`（40 族等价）、`tools/dev/check_tool_manifest.py --base origin/main`（47 族 / 282 条目 append-only）、`tools/dev/check-internal-ip-leak.py`、`scripts/run_gates.py`（`env-inventory` / `ruff`）均通过（worktree 无 `frontend/node_modules`，`eslint` 未在本 worktree 跑；未改前端）。

## Revisit

- PR 合入 `main` 后需执行站点发布与注册：
  1. 将 `spd_dump-20261118.tar.gz` 放入 `/mnt/stp-aee/packages/spd_dump/20261118.tar.gz`；
  2. 运行 `check_script_packages.py --publish --packages-root /mnt/stp-aee/packages` 发布 `flash_firmware_unisoc_bsl@1.0.5`；
  3. 触发 `POST /api/v1/scripts/scan` 并更新既有 Plan 的 step pin。
- 展锐关机态 `vbus_cycle` 需等机位具备「伪电池联动断电 + 硬件下载触发脚短接」改造后再另行立项。
