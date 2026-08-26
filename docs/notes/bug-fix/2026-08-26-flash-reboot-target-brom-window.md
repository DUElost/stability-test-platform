# flash_firmware reboot_target 默认值跳过 BROM 窗口

Status: implemented（flash_firmware v1.3.2）
Class: bug-fix

## Decision

`reboot_target` 默认值从 `"bootloader"` 改为 `"normal"`（不带 target 的普通
`adb reboot`）；`""` 与缺省同义于 normal；`"bootloader"` / `"fastboot"`
保留为显式选项。该参数自 v1.2.0 引入以来从未被真机验证，2026-08-25 于
172.21.15.66 首次真机执行即证伪。

## 机制

SPFT console 版只附着 BROM(PID 2000)。两种重启的 USB 呈现完全不同：

| 方式 | USB 路径 | 工具可见性 |
|------|---------|-----------|
| 普通 `adb reboot`（默认） | 完整上电 → 流经 BROM 窗口 → 正常启动 | **秒抓** → Download Succeeded |
| `adb reboot bootloader` | 热重启直达 fastboot/download 态（pid 201c） | 全盲 → 两轮 S_TIMEOUT(1042)，手机滞留 fastboot |

滞留 fastboot 的恢复手段：`fastboot reboot`（约 40s 回 adb）。

## Alternatives

- **保留 bootloader 默认 + 文档提示**：默认值就是错的，提示救不了派发即败。
- **脚本内探测失败后自动降级重发普通重启**：把一次可配置的语义变成隐式
  重试魔法，attempt 计数与真实失败原因都会被污染；不如显式改默认。

## Verification

- 单测 `test_flash_firmware_v132.py`：argv 形态断言（normal 不带 target、
  显式模式透传、设备不可达跳过）+ main() 缺省参数 wiring 冒烟。
- 真机对照实验（172.21.15.66，2026-08-25）：同机同包，bootloader 目标两轮
  S_TIMEOUT；普通重启秒抓 BROM 并 `Download Succeeded @36.20MB/s`，
  刷后 `sys.boot_completed=1`、版本与 manifest 一致。

## Revisit

- 其它机型族（ELA）接入时按同法各验一次 reboot 语义；若某机型 bootloader
  态确能被工具附着，再显式传参启用。
