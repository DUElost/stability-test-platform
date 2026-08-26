# 对固件组的需求：prop.default 增加 `persist.sys.usb.config=adb`

- 日期：2026-08-26
- 状态：**暂缓——实测现状已满足验收标准**。2026-08-26 串行实验（N=2，
  MLD-LX3 user_root 固件）显示 firmware-upgrade 刷完 ~10s 内 adb 自动在线
  （ro.secure=0 时 adbd 不受 persist.sys.usb.config 约束）。本文档保留：
  若未来切换非 root 测试固件，此需求重新生效。
- 影响机型：MLD-LX3（V552AA 系列）稳定性测试固件；后续 ELA 测试固件同要求
- 提出方：稳定性测试平台组

## 需求内容

在测试固件的 `prop.default`（或等价的默认属性文件）中增加一行默认值：

```
persist.sys.usb.config=adb
```

## 背景（为什么需要）

1. 平台对 MLD 机型的自动化刷机链路（flash_firmware）已上线，使用
   `firmware-upgrade` 命令，该命令会**格式化 userdata 分区**。
2. Android 的 USB 调试状态存于两处：`persist.sys.usb.config`（系统属性，
   决定 adbd 是否随 USB 启动）与 `Settings.Global.ADB_ENABLED`——两者都
   位于 **userdata 分区**。
3. 本系列固件的 prop.default **未提供这两个属性的默认值**。因此每次刷完：
   userdata 清空 → 标志归零 → 首次开机 adbd 不启动 → adb devices 完全
   看不到设备（连 unauthorized 都不显示）→ 必须人工在手机屏幕上开启一次
   USB 调试才恢复。

## 影响

- 每刷一台 = 一次人工碰屏幕。批量刷机（目标 60+ host / 数百台手机）时，
  这是自动化链路唯一无法闭环的人工环节。
- 附带问题：刷完的手机停在 OOBE 首页，长时间亮屏静置会自行关机
  （已实测），进一步放大人工跟进成本。

## 验收标准

任意一台刷完该固件、**不做任何手工操作**的机器，首次开机完成并回到
桌面后，`adb devices` 直接可见（state=device 或 unauthorized 均可接受；
unauthorized 仅需在平台侧确认一次指纹）。

## 边界说明

- 仅要求**测试/产线固件**携带该默认值；量产用户固件出于安全不考虑，
  与本需求无关。
- 本批测试机为 user_root 固件，无安全顾虑。

## 关联

- 平台侧对应记录：
  [Agent Note §5–§6](../../notes/feature/2026-08-25-mtk-flash-fleet-automation.md)
- OOBE 自动跳过已由平台脚本 `oobe_skip v1.0.0` 承接；本需求落地后，
  「派发即刷、刷完即可测」全链路无人值守。
