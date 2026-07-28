# 2026-07-28 172.21.9.126 硬挂事故复盘与处置记录

> **状态**：已闭环（host 已恢复，watchdog / EEE / BIOS / Agent restart 四项加固就位）。
> **影响范围**：单台 Agent host（`172.21.9.126`，Dell OptiPlex 7090，Service Tag H2884R3），无测试任务数据丢失，无控制面侧影响。
> **关联**：[`adr-0026-admission-and-scale-gray-rollout.md`](./adr-0026-admission-and-scale-gray-rollout.md) §AEE Reconciler 100% 启动崩溃（2026-07-25 已修，与本次硬挂**不同根因**）。

---

## 1. 现象与时间线

| 时刻（host PDT, UTC-7） | 时刻（控制面 CST, UTC+8） | 事件 |
|--------------------------|---------------------------|------|
| Jul 27 10:17:01 | Jul 28 01:17:01 | `journalctl --boot=-2` 最后一行：`CRON[201827]: (root) CMD (cd / && run-parts --report /etc/cron.hourly)`。下一行本应是 11:17:01 的 hourly cron，**整段缺失**。boot -2 启动时间 = Jul 9 02:04 PDT（连续在线 18 天 4 h）。 |
| Jul 27 10:17 … 19:43 | Jul 28 01:17 … 10:43 | 不可观测窗口 ≈ 9.5 h。控制面 Agent socket 掉线，`STP_DISCONNECTED` 类告警。无人工干预记录。 |
| Jul 27 19:43:24 | Jul 28 10:43:24 | host 重启：新 boot `-1` 起来。运维通过 SSH 接入排查。 |
| Jul 27 19:43 … 21:00 | Jul 28 10:43 … 12:00 | boot -1 期间执行四项加固（详见 §3）。21:00 PDT 触发 reboot 进入 boot 0 完成 BIOS capsule 写入。 |
| Jul 27 21:06:34 | Jul 28 12:06:34 | boot 0 起来（BIOS 1.42.0），watchdog / EEE / Agent systemd 单元均 active / enabled，Agent 重连控制面。 |
| Jul 28 14:08 CST | Jul 28 14:08 CST | 控制面侧检查：Agent 心跳恢复、socket 稳定。事故闭环。 |

---

## 2. 根因判定

判定结论：**整机硬挂死**（board-level hard hang），非 Agent 进程问题、非 OS 选型问题、非软 OOM/panic。

判据：

| 检查项 | 结果 | 说明 |
|--------|------|------|
| `journalctl --boot=-2` 末尾是否有 `Kernel panic` / `OOM` / `hung_task` / `MCE` / `general protection` / `segfault` | **无任一匹配** | `grep -iE "panic\|hung\|oom\|hardware error\|general protection\|segfault"` 在 boot -2 内仅命中 sudo/Ansible 行，无内核级厄运。 |
| journal 切点形态 | 在 cron 行（10:17:01）之后**瞬间**截断，**无 shutdown/reboot/poweroff 配对** | 软关机会有 `systemd-shutdown[1]: Shuting down`（注意拼写）+ `Journal stopped`；硬复位无任何 shutdown 序列。 |
| 上一 boot（`-3`，Jul 7）状态 | journal 标 `crash` | 同一主机 7 月曾发生相似硬挂，符合 BIOS/EEE 类平台级问题反复触发特征，而非单次偶发。 |
| boot -2 时长 | 18 天 4 h | 长期在线累积状态（链路低功耗切换、 cache 行老化等）才暴露的硬挂，与"长期运行后挂死"模式相符。 |
| Agent 进程是否在挂前崩 | 否 | journal 切点之前无 `stability-test-agent.service: Failed` / `Main process exited`，Agent 进程一直到最后一行都在正常跑。 |

排除清单：

- **非 Agent 代码问题**：Agent 主循环 `_shutdown_event.wait(poll_interval)` 无阻塞点；进程未被 systemd 杀、未 OOM。
- **非 OS 选型问题**：Debian 13 无 GUI 仅 ≈ 600 MB 常驻，曾因 Ubuntu GUI OOM 杀 Agent 才迁来此次 Debian 13 选型**正确**，与本次硬挂无关。
- **非 `sd_notify` 缺失**：曾考虑给 `stability-test-agent.service` 加 `WatchdogSec` 并补 `sd_notify`，但 sd_notify+WatchdogSec 只能挡主循环 tick 卡死，挡不住子线程死锁、更挡不住整机硬挂，覆盖范围不匹配当前根因，方案否决。
- **非网络抖动**：不是说"网络问题不会让 journal 截断"——而是网络问题不会让 journal 在 cron 行瞬间切断。journal 落盘由 journald 本地负责，与链路无关。链路问题可作为**触发**硬挂的诱因候选，但不是根因本身。

---

## 3. 已执行的加固措施（仅作用于 172.21.9.126）

以下四项于 boot -1 / boot 0 期间落地，**仅作用于本台 host**。原始配置已备份至 `/root/stp-watchdog-backup-20260728/`：

```
/root/stp-watchdog-backup-20260728/
├── system.conf.orig                       (Sep  3  2025 原始)
└── stability-test-agent.service.orig      (Jul 10 04:28 原始)
```

### 3.1 systemd 硬件 watchdog（核心）

**目标**：下次硬挂时不依赖人工恢复，板载 iTCO_wdt 在 10 s 后自动复位整机。

`/etc/systemd/system.conf.d/10-stp-watchdog.conf`：

```ini
# STP hardening: hard-hang auto-reset (added 2026-07-28)
# If systemd PID1 fails to update the hw watchdog for 10s, board resets itself.
# ABI: this is the kernel hw watchdog, not the per-service watchdog.
[Manager]
RuntimeWatchdogSec=10s
RebootWatchdogSec=5min
ShutdownWatchdogSec=5min
KExecWatchdogSec=2min
WatchdogDevice=/dev/watchdog
```

验证：

```
$ sudo dmesg | grep -i watchdog
[    0.512663] iTCO_vendor_support: vendor-support=0
[    0.525491] iTCO_wdt iTCO_wdt: Found a Intel PCH TCO device (Version=6, TCOBASE=0x0400)
[    0.525555] iTCO_wdt iTCO_wdt: initialized. heartbeat=30 sec (nowayout=0)
[    3.366453] systemd[1]: Using hardware watchdog 'iTCO_wdt', version 6, device /dev/watchdog0
```

> **说明**：内核侧 `heartbeat=30 sec` 是 iTCO_wdt 模块加载时的初值；systemd 接管后按 `RuntimeWatchdogSec=10s` 重新编程成 10 s。即 PID1 每 ≤10 s 喂狗一次，喂不动即硬件复位。这是覆盖整机的最后一道闸。

### 3.2 Agent service restart 强化

**目标**：即使 Agent 进程崩/被 OOM 杀，systemd 自动拉起，不留空窗。

`/etc/systemd/system/stability-test-agent.service.d/10-stp-watchdog.conf`：

```ini
# STP hardening (2026-07-28):
# WatchdogSec intentionally left at 0 — agent/main.py does not call
# sd_notify, so a non-zero WatchdogSec would false-positive kill it
# every 5min. Keep only Restart policy hardening.
[Service]
Restart=always
RestartSec=10
```

> **`WatchdogSec=0` 故意为之**：Agent 主循环不调 `sd_notify`，未声明 `WATCHDOG_USEC`；若设非零 `WatchdogSec` 会被 systemd 视为"5 min 内必须收到 sd_notify 否则杀进程"，结果就是每 5 min 假阳性杀一次 Agent。所以这里**只硬化 Restart 策略**，进程级心跳靠 `RuntimeWatchdogSec` 间接兜底（PID1 挂了才轮到它，已属硬挂范畴）。
>
> 若未来 Agent 加 `sd_notify("WATCHDOG=1", ...)` 主循环上报，可取消 `WatchdogSec=0` 改为 `WatchdogSec=30s`，与本配置兼容。

### 3.3 关闭网卡 EEE

**目标**：消除 Intel i219-LM 的 EEE 低功耗链路状态——这是 Dell 平台已知的一类硬挂诱因候选。即便它不是本次根因，关闭后定量降低复现概率。

`/etc/systemd/system/eee-disable-enp0s31f6.service`：

```ini
# STP hardening: disable Intel i219-LM EEE (added 2026-07-28)
# The i219-LM EEE low-power link state has known platform-hang vectors.
# Service runs once at boot after the NIC is up; failure is non-fatal.
[Unit]
Description=Disable EEE on Intel i219-LM (enp0s31f6)
After=network-pre.target
Before=network.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/sbin/ethtool --set-eee enp0s31f6 eee off

[Install]
WantedBy=multi-user.target
```

验证：

```
$ systemctl is-enabled eee-disable-enp0s31f6.service
enabled
$ sudo ethtool --show-eee enp0s31f6
EEE settings for enp0s31f6:
	EEE status: disabled
	Tx LPI: 17 (us)
```

> **NIC 设备名**是 `enp0s31f6`（i219-LM 板载）。其他机型若是不同网卡名，复用此 unit 时**必须改 ExecStart 里的接口名**，否则 oneshot 失败但 `RemainAfterExit=yes` 仍会"成功"开启而真没关掉 EEE。

### 3.4 BIOS 1.6.1 → 1.42.0（LVFS / fwupd）

**目标**：把平台固件拉到最新，覆盖 Dell 各次 capsule 修订的微码/CPU 微调/ME/ chipset 行为。

仪器：通过 [LVFS](https://fwupd.org/lvfs/) 用 `fwupdmgr update` 在线签名升级，**不依赖 Windows / 不依赖 U 盘**。Dell 官方签名 capsule，UEFI 写入时 KEK 由 2011 → 2023、UEFI dbx 一并更新。

| 项 | 升级前 | 升级后 |
|----|--------|--------|
| BIOS 版本 | 1.6.1 | 1.42.0 |
| 发布日期 | 2022-03-31（出厂） | 2026-04-23 |
| Secure Boot KEK | 2011 | 2023 |
| UEFI dbx | 旧 | 已更新 |
| `fwupdmgr get-updates` | 提示有 capsule | **No updates available** |
| fwupd 状态 | 未安装 | 已装 `fwupd 2.0.20`（static, 装包：`fwupd` `ethtool` `smartmontools`） |

升级流程（已记录在 host 操作 history 里）：

```bash
sudo apt-get install -y fwupd ethtool smartmontools
sudo fwupdmgr refresh          # 拉 LVFS metadata
sudo fwupdmgr get-updates      # 列出可升级 capsule
sudo fwupdmgr update           # 下载并 stage capsule 到 ESP
sudo reboot                    # UEFI 在开机阶段写入
# 重启后验证
fwupdmgr get-updates           # 应为 No updates available
sudo dmidecode -s bios-version # 1.42.0
```

---

## 4. 其余 19 台 host BIOS 汇总（2026-07-28 PDT 探测）

采集方式：从控制面并行 SSH 跑 `probe-bios.sh`（脚本临时落盘于 `/tmp/opencode/probe-bios.sh`，仅做 `dmidecode -t system / -t bios` + `which fwupdmgr`），单次完成。

### 4.1 机型分布

| 机型 | 数量 |
|------|------|
| OptiPlex 3090 | 14 |
| OptiPlex 3080 | 3 |
| Inspiron 3910 | 2 |
| OptiPlex 7090 | 1（即本次事故 host 9.126，已升级到 1.42.0） |

### 4.2 完整 BIOS 清单

| IP | 机型 | BIOS 版本 | 发布日期 | fwupd | 备注 |
|----|------|-----------|----------|-------|------|
| 172.21.8.87  | OptiPlex 3090 | 2.4.0 | 04/09/2022 | NO | ⚠ 出厂 BIOS |
| 172.21.8.103 | OptiPlex 3090 | 2.1.1 | 12/13/2021 | NO | ⚠ 出厂 BIOS |
| 172.21.8.116 | OptiPlex 3090 | 2.1.1 | 12/13/2021 | NO | ⚠ 出厂 BIOS |
| 172.21.8.143 | OptiPlex 3090 | 2.1.1 | 12/13/2021 | NO | ⚠ 出厂 BIOS |
| 172.21.8.192 | OptiPlex 3090 | 2.1.1 | 12/13/2021 | NO | ⚠ 出厂 BIOS |
| 172.21.8.195 | OptiPlex 3090 | 2.1.1 | 12/13/2021 | NO | ⚠ 出厂 BIOS |
| 172.21.9.6   | OptiPlex 3090 | 2.4.0 | 04/09/2022 | NO | ⚠ 出厂 BIOS |
| 172.21.9.93  | OptiPlex 3090 | 2.1.1 | 12/13/2021 | NO | ⚠ 出厂 BIOS |
| 172.21.9.112 | OptiPlex 3090 | 2.28.0 | 11/30/2025 | NO | 较新 |
| 172.21.9.114 | OptiPlex 3090 | 2.28.0 | 11/30/2025 | NO | 较新 |
| 172.21.9.116 | OptiPlex 3090 | 2.28.0 | 11/30/2025 | NO | 较新 |
| 172.21.9.124 | OptiPlex 3090 | 2.28.0 | 11/30/2025 | NO | 较新 |
| 172.21.9.127 | OptiPlex 3090 | 2.28.0 | 11/30/2025 | NO | 较新 |
| 172.21.9.128 | OptiPlex 3090 | 2.1.1 | 12/13/2021 | NO | ⚠ 出厂 BIOS |
| 172.21.9.123 | OptiPlex 3080 | 2.34.0 | 12/01/2025 | NO | 较新 |
| 172.21.9.131 | OptiPlex 3080 | 2.34.0 | 12/01/2025 | NO | 较新 |
| 172.21.9.132 | OptiPlex 3080 | 2.34.0 | 12/01/2025 | NO | 较新 |
| 172.21.9.117 | Inspiron 3910  | 1.0.3 | 11/13/2021 | NO | ⚠ 出厂 BIOS |
| 172.21.9.121 | Inspiron 3910  | 1.0.3 | 11/13/2021 | NO | ⚠ 出厂 BIOS |
| 172.21.9.126 | OptiPlex 7090  | 1.42.0 | 04/23/2026 | YES | ✓ 本次事故 host，已升级 |

### 4.3 风险分组

| 组 | 数量 | 主机 | 解读 |
|----|------|------|------|
| **A. 已升级 / 已是最新** | 1 | 9.126 | 本次事故 host。已在最新 BIOS + 全套加固。 |
| **B. 2025 末 BIOS（相对较新）** | 8 | 9.112 / 9.114 / 9.116 / 9.124 / 9.127 / 9.123 / 9.131 / 9.132 | 5× 3090@2.28.0（2025-11-30）+ 3× 3080@2.34.0（2025-12-01）。距 9.126 同窗口的 LVFS snapshot 不远，但未必是各机型的最新 capsule。需 `fwupdmgr get-updates` 实测确认。 |
| **C. 2021-2022 出厂 BIOS（高风险）** | 11 | 9 台 3090（7× 2.1.1 + 2× 2.4.0），2 台 Inspiron 3910（1.0.3） | 出厂已 3-4 年没动过；与 9.126 升级前同档"老固件长期在线"模式。按本事故根因类比，**具备相同硬挂诱因风险**，应优先升级。 |

> **汇总数据已修正**：早期口述漏数 2 台 2.4.0 + 2 台 Inspiron 3910，实际 2021-2022 出厂 BIOS 共 **11 台**，不是 8 台。

---

## 5. 后续推广建议（未执行）

> 这只是**建议清单**，本次事故处置范围仅限 §3 的 9.126 加固，其余 19 台 host 尚未落地任何加固项。每条都需走 PR + 灰度 + 回退预案才执行，不要照单全收。

### 5.1 推广顺序（按风险倒序）

1. **先装 fwupd 到 19 台**（一次性 apt 包，不动固件）。
   ```bash
   # 以现状 hosts.ini 为清单
   ansible android -i /home/debian13/hosts.ini -m apt -a "name=fwupd,ethtool,smartmontools state=present update_cache=yes" -b
   ```
2. **先做 watchdog / EEE / Agent restart 三项 OS 层加固**（§3.1 / §3.2 / §3.3 等价 drop-in 拷贝到全部 19 台）。这是**纯软件**，随时可回退（删除 drop-in + `systemctl daemon-reload` + reboot 验证），且**立即**给所有 host 兜底硬挂风险，不依赖 LVFS。
3. **再分机型批量探测 LVFS 最新 BIOS**：
   ```bash
   ansible android -i /home/debian13/hosts.ini -m command -a "fwupdmgr refresh && fwupdmgr get-updates" -b
   ```
   拿到每机型 LVFS 实际最新版本号后，与 §4.2 对照定分组。
4. **BIOS 升级分机型小批灰度**：每机型 1 台先升，跑 24 h 再升同机型其余台。

### 5.2 注意事项

| 项 | 说明 |
|----|------|
|Inspiron 3910 是否在 LVFS |Dell Inspiron 消费线机型**未必**进入 fwupd LVFS，需 `fwupdmgr get-updates` 实测。若无 capsule，回 Windows + Dell Update 或 U 盘 + Dell BIOS Recovery，**不**强升。 |
|OptiPlex 3090 / 3080 EEE 单元复用 | 必须重命名 systemd unit 或加 `ExecStartPre=/usr/sbin/ethtool --show-eee %I`，按实际接口名改成模板式 drop-in（推荐用 `networkd-dispatcher` 或 udev rule 接 %i 接口名），避免 copies 写死 `enp0s31f6` 在不同机型上失配。 |
|WatchdogDevice 路径 | 20 台不一定都是 `/dev/watchdog` → iTCO_wdt，部分机型若是 AMD 平台会是 `/dev/watchdog0` → wdat_wdt 等。建议先 `ansible ... -m command -a "ls -l /dev/watchdog*; sudo dmesg \| grep -i watchdog"` 收集后才定 drop-in 内容。 |
|Inspiron 3910 已无 sd_notify 假阳性问题 | 必须保留 `WatchdogSec=0` 的 Agent drop-in，**绝不**给 Agent 设非零 WatchdogSec 不开 sd_notify。 |
|升级窗口 | BIOS capsule 写入需要 reboot，host 上下行约 5-8 min。升级时务必先 drain 该 host 的 plan-run 任务（控制面 `POST /api/v1/plan-runs/hosts/{host_id}/drain`），再 reboot。 |
|Inspiron 3910 不在 §3.3 复用清单 | i219-LM EEE 关闭指令适用 OptiPlex 系列；Inspiron 3910 用的若是 Realtek / 不同 Intel NIC，`ethtool --set-eee` 可能不支持该选项，需先 `ethtool --show-eee <iface>` 看是否报 `Operation not supported`。 |

---

## 6. 回退路径

### 6.1 软件层（watchdog / EEE / Agent restart）

```bash
# 在 172.21.9.126 上撤销本次 OS 层加固（任一项均可独立回退）
sudo rm /etc/systemd/system.conf.d/10-stp-watchdog.conf
sudo rm /etc/systemd/system/stability-test-agent.service.d/10-stp-watchdog.conf
sudo rm /etc/systemd/system/eee-disable-enp0s31f6.service
sudo systemctl disable --now eee-disable-enp0s31f6.service 2>/dev/null
sudo systemctl daemon-reload
sudo systemctl restart stability-test-agent.service
# watchdog 设置回到默认需重启才生效
sudo reboot
# 验证：journal 不再出现 "Using hardware watchdog" 即已撤回
```

原始配置已备份在 `/root/stp-watchdog-backup-20260728/`，可 `sudo cp *.orig` 还原 systemd 配置。

### 6.2 BIOS 层

Dell BIOS 不可在线 downgrade（LVFS 元数据默认禁止 downgrade，需 `--allow-downgrade` 显式放开+签名校验）。**不计划回退**：

- 1.42.0 是 2026-04-23 发布的稳定 capsule，9.126 升级后已稳定运行 >2 h（boot 0 至发文时）。
- 若必须回退，走 Dell 官方 BIOS Recovery（开机 F12 → BIOS Recovery），**不**走 `fwupdmgr` 在线回退。

### 6.3 fwupd 包

```bash
sudo apt-get remove --purge fwupd ethtool smartmontools
# 或保留 fwupd，禁用其自动刷新 metadata：
sudo systemctl disable --now fwupd-refresh.timer
```

---

## 7. 复盘要点

- **硬挂的判定**：journal "在 cron hourly 行瞬间截断 + 无 shutdown 配对 + boot 标 `crash`"三连即可判硬挂，无需更多证据。"无 panic / 无 OOM / 无 hung_task"是排除项，**不是**用来确认硬挂的——硬挂时内核根本来不及落盘排错记录。
- **时区差**：host PDT（UTC-7）= CST（UTC+8）-15 h。控制面看到的"8 小时前断连"，换成 host 视角其实是更早的事件。后续排查 Agent 失联务必先对齐时区。
- **加固优先级**：**watchdog > EEE > BIOS**。watchdog 是覆盖最广（任何硬挂都能复位）、副作用最小（10 s 不喂狗即复位整机，正常负载下喂狗是 PID1 自动行为，无感）、回退最干净的兜底。BIOS 升级是治本但需要 reboot 且每机型需独立验证，落地慢。EEE 关闭是"性价比高的诱因消除"——不解决根因但定量减少触发机会。三者不互斥，按风险收益叠加。
- **不要用 `sd_notify` 假装"覆盖"硬挂**：进程级 `WatchdogSec` 只能挡主循环 tick 卡死，对子线程死锁、对整机硬挂都无效。给一个没有 `sd_notify` 调用的服务加非零 `WatchdogSec` 反而引入每 5 min 假阳性杀进程的负担。本次**故意**保留 `WatchdogSec=0`，靠 systemd `RuntimeWatchdogSec` 在 PID1 层面兜底，这是覆盖面更广且无假阳性的方案。
- **推广必须分机型灰度**：20 台机分 3 机型号 + 2 种 NIC（至少），不同机型的 EEE 行为、`/dev/watchdog` 实际设备、LVFS capsule 可用性都不同。不要假设 9.126 的 drop-in 可原样复制到 19 台。



