# flash_firmware 1.4.0：`trigger_mode=vbus_cycle` 切电刷机（#3892 U3）

Status: implemented
Class: feature

## Decision

规格是 [ADR-0063](../../adr/ADR-0063-vbus-cycle-flash-without-adb.md) D3。本单元以新版本 `flash_firmware@1.4.0` 落地；已发布的 1.3.20 及更早版本不动。

- 新步骤参数 `trigger_mode ∈ {adb_reboot, vbus_cycle}`，缺省 `adb_reboot`。
  - 缺省路径与 1.3.20 逐项一致，只在结果 metrics 多一个 `trigger_mode` 键；
  - 其余取值 fail-fast。
- 取锁前的闸，只做只读探测。任一不过就失败，且不做任何切电动作：
  1. 本机 `/etc/stp-agent/usb-power-slots.json` 有本 serial 的槽位，整份校验口径与 wrapper 相同。
  2. wrapper `capabilities` 含 `usb-power`。
  3. 设备已关机。按 Owner 2026-11-07 的三条答复：
     - adb `device` 态的设备，先按 serial 反查它所在的 USB 口，必须等于映射槽位口（映射写错时不得先关掉别的设备）；
       `adb reboot -p` 在取锁后、第一次切电前执行，等它从 adb 消失、槽位口回到可刷态或关机态；
     - 只有 `unauthorized` 直接拒绝：adb 关机与重启都做不到；
     - `offline`、`unknown` 等其它状态进切电流程，是否已关机交给下一条的 USB 判定；
     - 槽位口上已有除可刷态与关机态（`0e8d:2046`，Owner 现场 lsusb）之外的 USB 枚举，判为开机态，直接拒绝。这一条识别「开机但没开 ADB」的设备。
- 机型：关机设备读不到 `ro.product.model`。没有 `firmware_dir` 时，必须用步骤参数 `model` 声明机型。
  - 路由的其余判据不变：族表、`latest.json`、manifest `models`；
  - 刷后回读机型，与声明不一致判失败。
- 锁内的每次尝试：
  1. 槽位断电，等待 `vbus_off_settle_seconds`（10–30，下限对齐 #3892 的 10–15s 放电要求）；
     每次断电都核对 wrapper 回显的实际槽位，与脚本读到的不一致就停手，不再切电（包括隔离）；
  2. 起工具，在 `on_running` 回调里上电，不发 adb reboot；
  3. 门控与 V6 `-p` 用槽位口 `<hub_location>.<port>`，不按 serial 反查；
  4. 上电后 `vbus_brom_timeout_seconds` 内没捕获到 BROM，就杀工具，下一次尝试重新切电；
  5. 绑错口仍然立即终止，不重试。
  - 第 1 次尝试切电前，在锁内复核一次槽位口，防止有人在等锁期间开了机。
- 刷后阶段在锁内，各有独立硬超时：开机到 ADB（含 boot_completed）→ boot 稳定（沿用 v1.3.7）→ 跳过 OOBE → 版本核验。
  - OOBE 在脚本内实现，与 oobe_skip 同配方：两个 provisioning 标志，然后 force-stop setupwizard → 224 → 82 → 3；
  - vbus 下 `verify_version=false` 或没有目标版本时直接拒绝。
- 上电后发生任一失败、异常，或收到 SIGTERM/SIGINT/SIGHUP，都在 `_settle_lock` 里先断电隔离，再恢复门控、释放锁。
  - 只有全链成功才保持供电；
  - 一次切电都没做过（锁内复核失败）时不碰供电。
- 结果带 `vbus` 段：槽位、每次切电动作、阶段耗时、是否隔离、OOBE 回读。
- 共享函数的两处扩展都带缺省值，旧调用方不受影响：
  - `_run_flash_tool_with_progress` 新增 `poll_check`：工具一行不打也能判 BROM 超时；
  - `FlashToolAborted` 新增 `kind`。

## Alternatives

- 把 OOBE 跳过留给后续 `oobe_skip` 步骤：没有采用。主机锁不能跨步骤（ADR-0063 D3 第 5 条）。锁外开机的设备会把可捕获态暴露给下一任持锁者。
- adb 在线设备也直接切电：没有采用。断 VBUS 关不掉带电池且已开机的手机，上电不经过 BROM（Owner 2026-11-07）。
- 越界参数夹到边界：没有采用。夹逼会把手滑写成「能跑」。
- 锁内复核失败也断电：没有采用。那时还没有做过任何切电，设备是开机态，断电没有隔离意义。

## Verification

以下命令都在本工作树运行。
- `.venv/bin/python -m pytest backend/agent/tests/test_flash_firmware_v140.py -q`：43 passed。覆盖：
  - 参数闭集与越界；
  - 映射整份拒绝；
  - `poll_check` 杀树；
  - 取锁前各闸零切电；
  - 槽位口取门控目标（反查函数被替换成断言）；
  - 切电时序与 BROM 超时重试；
  - 绑错口不重试；
  - 刷后失败、信号路径隔离；
  - 缺省路径不调 usb-power；
  - 声明机型路由与刷后核对；
  - OOBE 配方；
  - 前置关机。
- 变异检查：分别去掉结算断电、adb 可见拒绝、BROM 超时重试分支，新测试各有 4、3、1 个失败。
- 复核 #3899（comment 6706217246）后补：adb 关机移入锁内（测试断言调用顺序为「取锁 → 关机」）、device 态端口核对、
  wrapper 回显槽位核对、`vbus_state` 定义提前到 `_settle_lock` 之前（信号路径不再可能 NameError）。
- `.venv/bin/python -m pytest backend/agent/tests/ -k flash -q`：288 passed。全部旧版本测试通过。
- `TEST_DATABASE_URL=…（本地隔离 PG16） .venv/bin/python -m pytest backend/tests/services/test_param_docs_content.py backend/tests/services/test_param_docs_registry.py -q`：132 passed，1 skipped。
- `python tools/dev/check_script_packages.py --register flash_firmware 1.4.0`：OK，39 个族树与 manifest 等价。
- `ruff check`：1 条 B023（`do_reboot`），1.3.20 原样存在，本单元未新增。
- `scripts/run_gates.py check:quick`：pending。本环境 frontend 未装依赖，eslint 找不到 `@eslint/js`，门禁在 eslint 处停下；本单元未改前端。
- 真机验收：pending。需要在 RTS5411 主机上做，见 ADR-0063 §6。

## Revisit

- 真机验收时确认两点：
  - 槽位映射的 `hub_location` 必须是 USB 2.0 侧的位置，否则门控口与工具 `-p` 对不上；
  - `adb reboot -p` 后的关机充电态在槽位口上是否不枚举。
- 关机态放行集合目前只有 `0e8d:2046`（Owner 2026-11-07 现场 lsusb，MLD-LX3）。开机但没开 ADB、同样呈现 2046 的设备在 USB 层无法区分，会在 BROM 捕获超时后失败并断电隔离。其它机型的关机态 PID 不同时，补到放行集合需要登记新版本。
- 第二期槽位入库时，脚本的映射读取改为平台下发，仍以 wrapper 侧校验为信任边界。
