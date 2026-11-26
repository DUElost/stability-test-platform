# unisoc_sysdump_config：展锐平台稳定性测试前自动开启 Full Sysdump

Status: implemented
Class: feature

## Decision

展锐（UNISOC）机型在 `Release_user_root` / `userdebug` 刷机后默认关闭完整内存转储
（`persist.vendor.sysdump=off`、`/proc/sprd_sysdump` 返回 `sysdump_status = 0`）。
为确保稳定性测试首次命中 Kernel Panic / Watchdog 时能够保留完整内存现场，新增版本化
Agent 脚本族 `unisoc_sysdump_config@1.0.0` 并接入全部 9 个稳定性测试内置模板
（`aging_ddr`、`ddr`、`gpu`、`monkey`、`monkey_watcher_patrol`、`mtbf`、`powercycle`、
`sleep`、`standby`）的 `init[2]` 位置（紧随 `check_device` 与 `sync_device_time` 之后）：

- 非展锐平台（MTK / 高通等）直接返回 `skipped=true`（`skip_reason="non_unisoc_platform"`），
  不触发 `adb root` 且无副作用；
- 展锐平台自动提权 `adb root`，检查 `/proc/sprd_sysdump` 与 `persist.vendor.sysdump` 幂等态，
  未匹配时执行 `setprop vendor.debug.sysdump.enabled true`（或 `false`）并轮询回读校验
  `persist.vendor.sysdump=on` 且 `sysdump_status = 1`；
- 刷机类模板（`flash_unisoc`、`flash_unisoc_bsl`）不接入该步骤，避免刷机阶段误入 Sysdump。

## Alternatives

- 由测试人员在开测前手工批量执行 `adb shell setprop`：易遗漏、刷机后 `persist` 复位会静默失效，
  且容易出现属性名拼写错误（如 `verdor`），否决。
- 塞进 `ensure_root` 或 `check_device`：会污染跨平台通用探活/提权脚本的单一职责，且
  `powercycle`/`sleep`/`gpu`/`ddr` 模板并未包含 `ensure_root`，否决。
- 仅靠 `minidump` 不开 Full Sysdump：无法满足疑难底层死机（内存踩踏、硬死锁）对完整 Ramdump 的诉求，
  按业务决策以默认开启 + 参数可关（`enabled: false`）落地。

## Verification

- 实机校准（P525AA `MLY-LX3-16-261119V343` `Release_user_root`）：
  - 核实 `/vendor/etc/init/systemDebuggerd.rc` 监听 `vendor.debug.sysdump.enabled=true/false`；
  - 实测 `setprop vendor.debug.sysdump.enabled true` 后 1 秒内收敛为
    `persist.vendor.sysdump=on` 与 `/proc/sprd_sysdump` `sysdump_status = 1`；
  - 实测 `setprop vendor.debug.sysdump.enabled false` 后回落为 `off` / `sysdump_status = 0`。
- 自动化校验：
  - `backend/agent/tests/test_unisoc_sysdump_config.py` 覆盖非展锐跳过、展锐提权开启+轮询收敛、
    幂等跳过、关闭、缺节点（默认跳过/严格失败）、回读超时、无 root 失败 8 条路径；
  - `tests/test_pipeline_template_script_pins_2865.py`、`backend/tests/api/test_pipeline_templates_stages.py`、
    `check_script_packages.py`、`check_tool_manifest.py`、`check:quick` 全通过。

## Revisit

- 后续若某展锐机型因固件无 SD/大容量导出路径或需要批量跑测连续性高于完整 Ramdump，
  在对应 Plan 的 `unisoc_sysdump_config` 步骤中显式设置 `params: {"enabled": false}` 即可覆盖。
- 若未来有展锐设备在长稳中因触发 Sysdump 停留在 `1782:4d00`，需结合主机侧巡检/告警提示先导出
  Ramdump 再复位，避免残留下载口阻塞同主机后续 BSL 刷机。
