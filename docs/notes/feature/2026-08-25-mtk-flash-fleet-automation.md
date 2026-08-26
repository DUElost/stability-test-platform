# MTK 批量刷机链路（SPFT Linux console）——可行性结论与环境前置条件

Status: implemented（flash_firmware v1.3.3；v1.3.0–v1.3.3 四个真机发现全部
闭环，2026-08-26 .66 默认参数端到端验收通过：attempt 1 抓中 BROM、
Download Succeeded @36.10MB/s、刷后核验一致、总耗时 154.55s）
Class: feature

## Decision

多台手机共用一台 Linux agent host 的刷机链路采用如下形态，已在 172.21.15.66
（直连口、单设备）端到端验证通过，在 172.21.15.87（hub 树、多设备）验证至下载阶段：

```text
[可选门控] 非目标 MTK 口 echo 0 > authorized   # 多设备同刷机态时消歧
flash_tool 无 -p 启动（进入 USB 轮询等待）
adb reboot 目标手机                              # 手机经过 BROM(PID 2000) 窗口
工具 15~16s 内抓中 → DA 上传 → 格式化 → 下载镜像 → Download Succeeded
```

配套事实（均为实测结论，非推测）：

1. **`-p` 参数不可用**：SPFT v5/v6/Selector v1.x 的 Linux console 版，
   `-p <bus-port.port>` 在扫描层能精确匹配三层拓扑设备名（如 `1-5.2.3`，
   即 sysfs 设备目录名），但打开层从不动作（lsof 证实 fd 从未建立），
   最终 `S_TIMEOUT(1042)`。V5(2021-05) 与 Selector v1.2444(2024-11) 行为一致。
2. **console 工具只附着 BROM 态（PID 2000）**：preloader(2001)、DA/HONOR
   download-mode(201c) 一律「命名端口但不打开」。`-b preloader`、
   option.ini `[Conn] DAPreLoader=true` 均无效。因此「先启动工具再让手机进
   BROM」的时序是硬约束——仓库现有 `adb reboot → 启动 flash_tool` 顺序正确。
3. **门控法代替 `-p`**：`authorized=0` 使设备从枚举消失、`1` 恢复并产生全新
   add@ uevent；工具对启动前已存在设备失明、对纯 add 波敏感，故门控后唯一
   可见设备必被命中。注意两条坑：
   - `authorized` 快速反复开关可能楔死 BROM 态手机的 USB 栈
     （`can't set config #1, error -71`），只能人工断电恢复。门控只动非目标口；
   - `authorized=0` 只对当前设备实例生效，手机侧重启/换槽位后新实例回到
     `auth=1`（门控会"漏"，但对单目标抓取无影响）。
4. **agent host 环境预检清单**（任一缺失即链路断裂，15.66 为活例）：
   - `flash_tool` 可执行位（hot-update 后可能丢失 chmod +x）；
   - Qt 运行库：`libXrender.so.1`、`libfontconfig.so.1`、`libglib-2.0.so.0`、
     `libgobject-2.0.so.0`、`libgthread-2.0.so.0`、`libSM.so.6`、`libICE.so.6`
     （最小安装 Debian 缺这些时工具直接起不来）；
   - `android` 用户在 `dialout` 组 + udev 规则
     `KERNEL=="ttyACM*", ATTRS{idVendor}=="0e8d", MODE="0666"`：
     否则 BROM 握手期 `Create COM File failed (EACCES)`；
   - adb server 端口按 host 探测（5037/5039 并存过），不要硬编码。
5. **刷后 adb 在线情况（2026-08-26 串行实验修正，N=2）**：当前 user_root
   固件（MLD-LX3 V552AA 系列）刷完 firmware-upgrade 后 **adbd 随首启自动
   在线**（~10s 内 get-state=device、boot_completed=1）——早前「连
   unauthorized 都不显示」的记录不成立于本固件（ro.secure=0 时 adbd 不受
   persist.sys.usb.config 约束；该结论可能来自非 root 固件样本）。真正的
   后置问题只有一个：手机停在 OOBE 首页且静置会自行关机（见 §6）。
   `persist.sys.usb.config=adb` 的固件组需求因此降级保留——若未来切换
   非 root 测试固件需重新提出：
   [firmware-requests/2026-08-26](../../operations/firmware-requests/2026-08-26-persist-sys-usb-config-adb.md)。
6. **OOBE 界面静置会自动关机（2026-08-26 实测）**：刷完机后手机停在 OOBE
   首页，长时间亮屏无操作会自行关机——表现为「手机无故掉出 adb」，实为
   OOBE 页的省电策略。SOP：adb devices 认到设备且进入 OOBE 后立即执行
   `/data/apk-repo/incoming/firmware/OOBE.bat` 中的命令跳过 OOBE 进主界面：
   ```text
   adb -s <serial> shell settings put secure user_setup_complete 1
   adb -s <serial> shell settings put global device_provisioned 1
   adb -s <serial> shell settings put system system_locales en-US
   adb -s <serial> shell am force-stop com.google.android.setupwizard
   adb -s <serial> shell input keyevent 224   # 唤醒
   adb -s <serial> shell input keyevent 82    # 解锁
   adb -s <serial> shell input keyevent 3     # HOME
   ```
   前置条件：等 `sys.boot_completed=1` 再执行——get-state==device 在 DA
   混合态就会放行，命令发太早会被初始化完成的 SUW 抢回前台；标志位也不会
   让已在前台的 SUW 自行退出，必须显式 force-stop。
   批量刷机的 Plan 应把该步骤编排为 flash_firmware 之后的固定一步。
   已平台化：`oobe_skip v1.1.0`（每条命令强制 `-s <serial>` 只打目标设备，
   与 bat 的全 host 广播语义相反；含 boot_completed 门、SUW 清场、
   标志位回读核验与 ui_focus 诊断）。

落地：`flash_firmware v1.3.0`（`backend/agent/scripts/flash_firmware/v1.3.0/`）。
四个新参数（均有 `STP_FLASH_*` env 逃生键，进 hot-update fleet 白名单，
空值不推）：

| 参数 / env | 默认 | 语义 |
|------------|------|------|
| `gate_other_mtk` / `STP_FLASH_GATE_OTHER_MTK` | true | 门控非目标刷机态 MTK 口；目标口在 adb reboot **前**按 serial 经 sysfs 反查（BROM 态无 serial）；无 serial / 非 Linux 时跳过并记录原因 |
| `max_attempts` / `STP_FLASH_MAX_ATTEMPTS` | 2（cap 4） | 整链路重试环：每次 = 重启 + flash_tool + 等结果；launch 类环境错误不重试 |
| `retry_backoff_seconds` / `STP_FLASH_RETRY_BACKOFF` | 10 | 相邻尝试间隔；attempt>1 会重画门控（authorized 只对当前实例生效） |
| `strict_env_check` / `STP_FLASH_STRICT_ENV_CHECK` | false | 环境预检（可执行位/ldd 缺库/adb/ttyACM 写入路径）默认宽松，ttyACM 不明仅 WARNING |

metrics 新增 `attempts[]` / `attempt_count` / `gating` / `env_precheck`，
v1.2.0 全部顶层 metrics 键保留（取最后一次尝试值）；输出契约
（success/skipped/PROGRESS 戳语义）不变。门控恢复走 try/finally，
覆盖超时/异常路径。host 级串行仍由 `/tmp/stp-flash-firmware.lock` 保证。
单测 `backend/agent/tests/test_flash_firmware_v130.py`（sysfs 树注入
tmp_path 复刻 .87 hub 树拓扑）；seed 迁移
`a7b8c9d0e1f2_seed_flash_firmware_v130_params.py`（deactivate v1.1.0，
保留 v1.2.0 作回滚）。部署日真机回归发现路由表缺连字符机型键，由 v1.3.1
承接（[bug-fix note](../bug-fix/2026-08-25-flash-route-table-hyphen-models.md)）。

## Alternatives

- **`-p` 参数指定端口**：放弃。打开层缺陷为工具跨代问题（已向 MediaTek
  报告的证据链齐备：QT log + GLB log + lsof + 复现步骤）；且无-p + 门控
  完全覆盖其用途。
- **USB authorized 反复开关目标口触发重枚举**：仅限非 BROM/preloader 态
  使用；BROM 态有楔死风险（15.87 的 1-5.4.1 曾因此需人工插拔）。生产流程
  用 adb reboot 天然产生全新枚举，无需碰目标口。
- **旧版工具（Radxa V5 2021-05 / 公开 V6.2228 2022-07）**：无收益，`-p`
  行为与现版本相同，且对新平台 DA 支持更差。
- **Windows 工位刷机**：`-p` 功能正常但脱离 agent 自动化体系，仅作人工兜底。
- **config.xml console 模式**：未发现其连接层行为差异，徒增配置面。

## Verification

- 端到端成功样本：15.66 直连口，`Download Succeeded`（super.img 3.30G 完整），
  日志 `/tmp/direct_flash.log`；重启后 105s adb 自动回归（persist 属性已写入）。
- `-p` 缺陷取证：`grep -a "com portName\|Searching user specified" <log>` +
  `sudo lsof /dev/ttyACM*`（命名后 fd 恒空）；对照 V5/Selector 双版本一致。
- 门控有效性：19 台 MTK 口批量 `authorized=0` 后工具视野内刷机态设备数为 1，
  弹入目标即被命中（两次实验均 15~16s 连接）。
- 权限问题定位：GLB log `comm_engine.cpp errno 13 Permission denied` +
  `<ERR_CHECKPOINT>[809][error][0xc0010001]`。
- .87 hub 树硬件问题定位：全天所有会话（含操作员手工）下载中途
  `STATUS_DOWNLOAD_EXCEPTION`，无一完整；dmesg 多次
  `disabled by hub (EMI?)`；失败后手机经 hub 无法重枚举（`error -71`），
  直连口对照可完整刷毕 → 排除软件因素。

## Revisit

- MediaTek 发布修复 `-p` 打开层的 Selector 新版本时：重测后可用 `-p` 替代
  门控（并行刷机的前提）。
- 15.87 更换第一级 hub/线缆/电源后：用一次 ≥3.3GB 完整刷机复测 EMI 结论。
- 固件组在 prop.default 加入 `persist.sys.usb.config=adb` 后：删除首开机
  手动开调试的工位 SOP。
- HONOR download-mode(201c) 若确认可被新工具附着，preloader 残局机的
  远程复活路径可重议（当前只能断电）。
