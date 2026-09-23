# 控制面宿主 OOM/卡死防线：撤 dryrun + 武装 watchdog + 测试内存硬顶（#3200）

Status: implemented
Class: process

> 落地范围：本机已生效；仓库侧事实源 + **安装清单/漂移检测已接**（#3200 T1 完成，见下）。

## Decision

2026-09-23 13:42–13:46 CST 控制面宿主整机卡死（swap 只剩 658 MiB、avail 1.8 GiB，元凶是
`pytest backend/agent/tests -v` 单进程 120 s 内 anon 2.6 GiB→15.3 GiB），人工强制重启收场。
同形事件：2026-08-03 三次（#123）、2026-09-14 09:14。事后核出的事实是**四层防线当时全空**：
earlyoom 在 `--dryrun`（只看不杀，打了 307 行低内存日志零动作）、硬件 watchdog 未武装
（`RuntimeWatchdogUSec=0`，07-28 `.126` 事故的那四项加固从未覆盖控制面宿主）、宿主内存告警
仍是仓库外草案且 `for:` 长于终局阶段、而 `test-env-self-check` skill 教的正是那条致死裸命令。

本单落三件，落在**不与他人 in-window scope 撞面**的最小集合上：

1. **`deploy/control-plane/host-defense/`**：`earlyoom.default` 与 `10-stp-watchdog.conf` 进仓
   当事实源 + `README.md` 给安装与**验证**（`RuntimeWatchdogUSec` 读回、`dmesg` 的
   `Watchdog running with a hardware timeout`、`fuser /dev/watchdog0` 必须是 PID1）。
   两处对 09-14 草案的实质修正来自本次证据：earlyoom 匹配的是 **comm**，失控体
   `comm=python` 不在旧 `--prefer` 表里（死前它锁定的候选是 24 MiB 的 zcode 而不是 15 GiB 的
   元凶）；失速窗内 earlyoom 自身遭遇 `cgroup: fork rejected by pids controller`，故去 `-n`
   加 `-p`。
2. **`test-env-self-check` §3 与 `testing.md` §2**：把 `systemd-run --user --scope -p
   MemoryMax=6G -p MemorySwapMax=0` 从 #123 里的「建议」升成**缺省姿势**，并写明
   「被顶杀死是结论不是障碍」——加大上限续跑会把定位机会洗掉。
3. **`testing.md` §7 新增 (c) 条**：假时钟推进型夹具（`_patch_advancing_clock`，2026-09-17
   Note 为压墙钟引入）使 `deadline` 不再构成上界——同一条失控循环的表现从「耗秒」变成
   「耗内存」。要求等待/重试环另有**与迭代计数挂钩的上界**。

### T1 追加（同日晚些：把防线从「手工资」变成装配判据）

PR #3201 落的是事实源，但资产仍靠手工重放、且无漂移检测——正是 #3050 批过的「执行者只在
本机」。T1 把它们接进装配链：`stages.py` 新增 `host_defense_artifacts()/host_assets()`，
S1 装包 → S2 渲染（渲染期即拒绝生效行带 `--dryrun`）→ S4 落盘 + `restart earlyoom` +
`daemon-reexec` + **回读验证**，任一不过即 `install_host_defense` 红灯；漂移检测面从
`monitoring_artifacts()` 改指 `host_assets()`（同一事实不留两套清单）。

一处**有意改变安装语义**的决定：防线不挂 `monitoring.enabled`。观测开关可以关，自动复位
不能关；代价是所有站点从此要求 earlyoom 包（缺包即 S1 FAIL，不静默降级）。

一条自己踩到的判据缺陷：`--dryrun` 守卫最初按整文件子串判，而解释性注释里正提到这个旗标
⇒ 装配期自我触发，38 条测试同时红。改为只判 `EARLYOOM_ARGS=` 生效行（`testing.md` §7
「源扫描型守卫」同族的反向教训：**判形态先要判准作用域**）。

## Alternatives

- **只撤 dryrun、不装 watchdog**：否决。earlyoom 只在 avail≤15% **且** swapfree≤8% 时动作，
  失速形态下它自己都会 fork 不出进程；本次 13:42 它就没杀成任何东西。无 watchdog ⇒ 仍要人到场。
- **给 `user.slice` 设全局 `MemoryMax`（资源边界）**：否决（本轮）。owner 2026-09-22 在 #3050
  已裁「换页风暴本体按工作模式稳态占用接受，不收敛常驻、不设资源边界」；越界改法需要新裁决。
- **把三条宿主内存告警直接抄进 `alerts-stability-platform.yml`**：否决（本 PR 不做）。它同时
  撞 `test_alert_metric_producers`（`node_*` 无本仓生产者）、`test_prometheus_alerts_contract`
  （每条规则需 promtool 场景 + 选择器对指标注册表）、`test_alert_count_claims_are_live`
  三条判据，且 `deploy/prometheus` 与 `alerts-stability-platform.yml` 正由 #2959/#3098/#2978
  与 PR #3199 在窗修改——按 §3.4 避让，G1 留在 #3050。
- **`reexec` 换成 `reboot` 让 watchdog 生效**：否决，生产控制面不可为；`daemon-reexec` 即足够
  （PID1 重读 manager.conf，已实测）。
- **改 `vm.swappiness` / ADR-0047 池参数**：否决。ADR-0047 D1/D2 未裁决，不得由一次事故处置顺带改。

## Verification

```bash
# 本机防线（P0，2026-09-23 14:20 起）
tr '\0' ' ' </proc/$(pgrep -x earlyoom)/cmdline | grep -o -- --dryrun   # 无输出 = 不再是空转
systemctl show -p RuntimeWatchdogUSec --value                          # 30s
dmesg | grep -i "Watchdog running with a hardware timeout"             # iTCO_wdt 30s
sudo fuser /dev/watchdog0                                              # PID 1
# 实测修正：systemd 257 的 system.conf **没有** ShutdownWatchdogSec 键（写了被静默忽略）
```

- 元凶归因（事故后 27 分钟现场目击同形复现）：`.wt/stp-adr0051-phase3` 上
  `pytest backend/agent/tests/test_powercycle_scripts.py` 49 s→7.7 GiB、65 s→10.1 GiB
  （≈160 MB/s），SwapFree 7.2→2.5 GiB；`main` 上同文件 **68 passed / 0.09 s** 无增长
  ⇒ 失控在 WIP 分支，非 main 回归。修复归 PR #3199 在窗方（其 scope 含该文件）。
- 有界复现（全程 `MemoryMax` 硬顶 + `MemorySwapMax=0`，未伤整机）：main 套顶跑目标文件
  0.09 s 通过；phase3 套顶跑为一整行 `FFFF…` 失败但被上限托住——即 §2 姿势的正面样本。
- 文档/门禁：`python scripts/run_gates.py check:quick`（结果见 PR Agent Note 表）；
  `python -m pytest tests/test_skill_usage_report.py -q`（skill 面守卫）。

## Revisit

- ~~T1 尾账~~ **已完成**：资产进 `host_defense_artifacts()`，安装链含生效回读，
  漂移检测覆盖（本机实测 11 match / 0 drift）。`earlyoom` 成为控制面宿主的硬前置（缺包即红）。
- **G1（#3050）**：宿主内存告警需按现网分布重标为**结果判据 + 短 `for:`**——本次实测终局只有
  2 分钟（13:40→13:42），`HostMemAvailableLow(for:10m)` 结构上不可能响，`HostSwapFreeLow`
  在死前 14 分钟自行消警；而 `node_pressure_io_waiting` 全窗 ≈0.99 早已成立。
- **R2 观察点**：earlyoom 现在是真的了，但**它是否真会选中失控体**尚未被一次真实击杀证明
  （09-22 裁定的 R2 触发即指此事）。首次真实击杀后回到本条核对：若仍选中别的进程，需要改用
  `--sort-by-rss` 或收紧 `--avoid`。
