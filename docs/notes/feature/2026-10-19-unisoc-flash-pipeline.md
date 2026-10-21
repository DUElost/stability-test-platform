# Agent Note: 展锐（Unisoc）自动化刷机流水线

Status: implemented
Class: feature

## Decision

新增展锐平台整包刷机能力，形态对齐既有 MTK `flash_firmware`：

1. **脚本族** `backend/agent/scripts/flash_firmware_unisoc/`（登记到 v1.0.6）。
   传输走 `fastboot -s <serial>`，不依赖 `sprd_usb` 内核模块，也不需要按键进 BSL；
   同 host 串行由 host 锁保证（见 1.0.6）。六处运行期加固都来自真机：
   - **fastboot 预检**（1.0.1）：进 bootloader 前先定位 fastboot，找不到就失败，
     避免把设备留在 fastboot 而 agent 无法自救；
   - **恢复模式**（1.0.2）：启动时 adb 不可见但 fastboot 能看到 serial → 用显式
     `family+version` 继续整包刷写，跳过 getprop/SPL 比对；
   - **字符流进度**（1.0.3）：fastboot 的 `Sending sparse 'super' k/N` 用 \r 覆盖
     同一行，按行读要等进程结束；改 `os.read` 后按缓冲区最后一个 k/N 打 PROGRESS
     戳，12.75 GiB 的 super 不再触发步骤级 stall。
  - **慢枚举容错**（1.0.4）：`getvar current-slot` 就绪探测从 10s 提到 30s 且
    **非致命**——172.21.15.75 实测首次应答要 11–22s（12+ 设备共用 USB hub），
    旧实现把「枚举慢」变成抛异常穿透 `wait_fastboot`/`probe_device` 的等待窗口，
    run 1043/1044/1045 与 host 侧 CLI 恢复都在刷写开始前中断、设备滞留 fastboot
    （#3758 真机证据）。超时现在只算「还没就绪」，预算交给外层等待窗口
    （probe 的 fastboot 窗口 30s→90s）。
  - **reboot 幂等收尾**（1.0.5）：全部写入完成后 `fastboot reboot` 在慢枚举
    host 上要等设备离开总线才返回，旧 20s 预算把「已写完」判成失败、批量整盘
    重刷（15.75 实测 12:37：super/wipe 全部落盘后 `timeout after 20s: reboot`）。
    现在 reboot 超时先探一次设备是否还停在 fastboot：离开总线＝reboot 已生效，
    继续 adb 回读核验；仍在＝显式重试一次，再失败才致命。
  - **host 锁**（1.0.6）：与 MTK `flash_firmware` 共用 `/tmp/stp-flash-firmware.lock`
    （flock，O_NOFOLLOW，LOCK_NB 轮询每 5s 打 `lock-wait` 戳，等待对 stall 钟可见）。
    Owner 裁定（2026-10-21）：展锐 fastboot 没有 flash_tool 式 USB 口绑定能力
    （SN→COM 经 adb devices/lsusb 映射不可靠），**同 host 并发禁止**——单 host 串行、
    多 host 并行由锁内建，不再依赖外置编排。锁随进程退出由内核释放。
2. **固件产物** 由 `tools/dev/prepare_unisoc_firmware.py` 从 PAC 生成，落
   `{NFS}/firmware/<family>/<version>/`（manifest.json + images/plan.json +
   raw 镜像），族级 `latest.json` 按 `ro.product.model` 分流，与 MTK 固件同一套指针约定。
3. **刷写范围** = 厂商 `firmware-upgrade` 等价面：uboot/sml/trustos/teecfg +
   modem/DSP/PM/logo + boot/init_boot/vendor_boot/dtbo/vbmeta* + super/cache/blackbox，
   共 24 个分区；SPL（eMMC boot0）走 `adb dd`，写前比对、写后回读 sha256。
4. **出厂态** = `erase userdata + metadata + misc`，保留
   ProdNV/Persist/NV_LTE/DELTANV/UserData（IMEI/校准）。**刷完必须接
   `oobe_skip`**：新擦除的设备停在开机向导，静置一段时间会自行关机
   （与 MTK 原生流程一致），模板 `flash_unisoc.json` 已固定
   `flash → oobe_skip → check_device` 三步。
5. **只升级**：`version_key` 比对目标/当前版本，降级默认拒绝（`allow_downgrade` 可放开）；
   同版本默认 skipped，`force=true` 可重刷（幂等已实测）。

## 刷机前置并轨（2026-10-21，#3758 采纳）

- Hosts 页「刷机前置」按钮（`ensure_flash_prereqs.yml`，RunConsole，可批量）此前只装
  MTK 面（SP Flash Tool Qt/GL 库 + 0e8d ttyACM + Android USB 节点规则）；fastboot 二进制
  不在包集，是 .76/.77「刷机即挂」的根因（run 1030 型）。本批把 **`fastboot` 纳入刷机
  前置包集**：`flash_preflight._UNISOC_PACKAGES` 为事实源，group_vars
  `agent_flash_prereq_packages_unisoc`、两 playbook 循环、install_agent.sh 链、守卫测试
  四路同源对拍（`flash_preflight@1.0.8`）。合入 main 并重部署站点前，站点侧按钮暂不含
  展锐包集——新 host 接入时仍需按本 note 补装或等合入。
- Plan 编排位并轨 MTK 流程：新族 **`flash_preflight_unisoc@1.0.0`**（fastboot 解析 / adb
  就绪 / **identity 硬匹配**（防 A57 Pro/A77 互刷，序列号形态不可信）/ 电量闸门（默认 ≥5%，
  Owner 裁定排除电量 0）/ latest.json+manifest models 可读；host 锁占用只报告不等待，
  排队交给 flash 步的锁）。模板 `flash_unisoc.json` 变为
  **preflight → check_device → flash(1.0.6) → oobe → check**（pin 守卫名单已同步）。
- 批量战役（31 台 B17→0818，2026-10-21 发起）：多 host 并行、host 内串行、canary 先行、
  失败即停该 host 队列。外置驱动 `~/a57_fleet.py` 与结果 `~/a57_fleet_results.json`。

## Alternatives

- **BSL / spd_dump 全量刷写**：需要 `adb reboot autodloader` 或按键进下载模式，
  且 spd_dump 的 FDL1 上传中断会让设备卡在 download mode（本次实验实际踩到，
  只能断电恢复）。作为兜底保留，不作为流水线主路径。
- **官方 DLoader**：走 ttyUSB + 自编译 `sprd_usb` 老驱动（6.12 内核有编译风险），
  且无法与 STP 的 per-device 并行模型对齐。
- **复用 `flash_firmware` 加 platform 分支**：MTK 脚本 119 KB 且强绑定 flash_tool
  的 USB 口绑定/门控语义，混入展锐只会让两条链路互相牵制；按脚本族分开。
- **把 prepare 做成 Agent 侧即时解包**：10.5 GB PAC + 12.75 GiB raw super 每台
  设备重做一次不可接受；改为版本级一次性产物放 NFS。

## Verification

- 真机（ZTE P615F03 / ums9230，serial 6R0A57SSAE7000348）：B17 → B19 两次完整刷写
  （本地产物 / NFS 指针路由各一次），24 分区、擦除、首启、回读全绿；
  `verifiedbootstate=green`、`user_setup_complete=0`、`persist` 与刷前逐字节一致。
  时间：本地 493 s、NFS 605 s（super 12.75 GiB 占大头）。
- 产物校验：`plan.json` 40 条 / 26 条可刷；raw 镜像与 `simg2img` 输出逐字节一致
  （修复了 FILL chunk 长度写错导致 super 损坏、LK 落 fastboot 的缺陷）。
- 测试：`backend/agent/tests/test_flash_firmware_unisoc.py`（13 例，覆盖版本比较、
  latest.json 变体、manifest 校验、fastboot 预检、adb/fastboot 探测、刷写顺序、
  preserve 过滤、SPL 判定、wipe）、`tests/test_prepare_unisoc_firmware.py`（3 例，
  钉住 sparse FILL/DONT_CARE/RAW 语义）、既有模板校验全绿。
- v1.0.4/1.0.5 回归（2026-10-21）：套件扩到 18 例——`FakeFlakyCmd` 复刻
  `Cmd.run` 超时抛 `RuntimeError` 的真实形态，钉住 ① `probe_device` 首次
  getvar 超时后仍能在窗口内落回 fastboot 模式；② `wait_fastboot` 连续两次
  超时不致命；③ `boot_slot` 单调用预算必须高于 10s；④ reboot 超时 + 设备已
  离开总线＝不重发；⑤ reboot 超时 + 设备仍在 fastboot＝重试一次。模板 pin 同步 head，
  `test_pipeline_template_script_pins_2865`（7 例）与 `check_script_packages`
  / `check_tool_manifest --base origin/main` 全绿。

### 真机 Plan 运行（2026-10-19，host 172.21.15.75，device 6R0A57SSAE7000295）

| run | 结果 | 证据 / 处置 |
|---|---|---|
| 1030 | FAILED | 该 host 未装 fastboot → 1.0.1 的预检在重启前拦下，设备未受影响 |
| 1031 | FAILED | super 阶段 600s 零打戳被 stall 杀掉 → 设备停在 fastboot；由此补 1.0.2 恢复模式 + 1.0.3 字符流进度 |
| 1032 | FAILED（queued） | 设备 OFFLINE，dispatcher 按 fail-closed 拒发；改用 host 侧跑 1.0.2 包恢复（冷刷 2519s） |
| 1033 | **SUCCESS** | 24 分区、flash 726.6s、wipe、boot、`version=B19`/`green`/`setup_complete=0`，check_device 通过 |
| 1036 | **SUCCESS** | 模板接入 `oobe_skip` 后 B17→B19：SPL 实写、24 分区、flash 573.8s；oobe 步 `setup_complete=1`/`device_provisioned=1`，焦点离开 SetupWizard |

- 冷刷（super 首次从 NFS 读）约 42 分钟，热刷约 15 分钟；步骤 `timeout_seconds`
  按 3600 配，模板已同步。
- host 准备：`apt install fastboot`（Debian 13，1:34.0.5-12），经 ansible 下发。

## Revisit

- SPL 写入的断电风险：目前 `flash_spl=auto` + 回读校验，但写 boot0 期间掉电仍需
  按键进 BSL 恢复；产线若要彻底规避，需要 UPS/夹具保障或改回 BSL 单一路径。
- 路由表目前只登记 `Z2581` / `P615F03` → `A57`；新增机型要补
  `_MODEL_FAMILY_ROUTES` 并登记脚本新版本。
- NFS 余量：单版本 24 GB，`/mnt/stp-aee` 已用 93%，第二个版本前需扩容或制定旧版本清理策略。
- 降级能力未验证（AVB rollback 未测）；确有降级需求时另开单元。
