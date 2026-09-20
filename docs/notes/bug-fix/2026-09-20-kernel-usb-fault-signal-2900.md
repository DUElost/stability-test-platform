# 内核 USB 子系统故障的 host 级信号（#2900：xHCI 死亡 / 慢性链路劣化）

Status: implemented
Class: bug-fix

## Decision

xHCI 主控报 `HC died; cleaning up` 后**整机 USB 对内核永久不可见且不自愈**，而 SSH /
systemd / Agent 心跳全部正常 ⇒ 控制面 ONLINE/HEALTHY、设备产能静默归零（fleet 内 3 例，
最长失明 11 天）。设备数口径看不见这类失明——三例里主机本来就没有设备可数。本单把
**内核日志**变成 host 级信号。

**判据（`backend/agent/kernel_usb_faults.py`）**

- `usb_host_controller_dead` = 「内核报过主控死亡（`HC died` 或 `xHCI … not responding`）」
  **∧**「此刻 USB 一台都看不到」。两个条件缺一不可：只看日志会把「死过但已 unbind/rebind
  救回」的 host 永久标红；只看设备数会把「本来就没接设备」误判。**设备回树即自动回落**
  （latch 在 `poll` 里被清），不需要人工清位——对应 issue 的「恢复后回落到 HEALTHY」。
- `usb_link_degraded` = 窗口内 `error -71/-110` ≥ 20 条或 `Maybe the USB cable is bad?`
  ≥ 5 次（.102 死前两周每日 400~3700 行，本阈值可提前定位，而单次插拔抖动不触发）。
- `usb_device_count is None`（`lsusb` 失败）**不主张失明**——未知 ≠ 0，与
  `device_discovery.count_usb_devices` 同口径。

**扫描语义**：首扫读**整段 boot**（`journalctl -k --boot`）——覆盖「开机即死」与
「Agent 重启时主机已死」；其后按 `--since <上次扫描开始时刻>` 增量读（游标先推进再扫描：
宁可重复读，不可漏读扫描期间的事件）。节流 60s（心跳 5s 一拍，内核日志不必每拍读）。
**扫描在后台线程**：`journalctl` 在坏盘/大日志下可能秒级，心跳主循环被拖慢会被
session_watchdog 判 OFFLINE——那是比失明更响的误报。扫描失败（无权限/超时/无 journalctl）
一律 `None` ⇒ 本拍不报也不清状态，并在首次失败留一条 warning。

**上报落点**：复用既有 `health.reasons` 通道（`capacity_reporter`）——warning 级 reason
只把 host 拉成 DEGRADED，**不进 `_compute_health_limit` 的打闸判据**（「零设备该不该禁调」
是 #2902 的门禁议题，与本单正交）。前端补 `REASON_LABELS` 两条中文标签。

**不写告警规则**：平台告警文件当前**没有加载路径**（#2880：`alerts-stability-platform.yml`
停在「ADR-0011 待挂载」），写进去只会是「留了文件 ≠ 有检测」。规则待 #2880 落地后补。

### 实测发现（改变了实现）

本机实测：`dmesg_restrict=1`，且当前用户不在 `adm` / `systemd-journal` 组时，
`journalctl -k` **exit 0 且 stdout 只剩 1 行、stderr 打一行提示**
（"You are currently not seeing messages from other users and the system / Users in groups
'adm', 'systemd-journal' can see all messages."）——**与「内核干净」长得一模一样**。
这正是本单要治的失明形态，故扫描器显式识别该提示并返回 `None`（未知），
用例 `test_permission_hint_is_unknown_not_clean` 钉住。

## Alternatives

- **host 侧落 node-exporter textfile 指标 + Prometheus 告警**（仿 `stp-pg-guard`）：弃——
  站点 Prometheus 的唯一 job 是 `file-server → 127.0.0.1:9100`（`prometheus.yml:25-30`），
  **不抓 agent host**，host 上写的 textfile 无人抓；走控制面 DB 现算指标则要动
  `core/metrics.py` / `routes/metrics.py`（#2873 正在改这两处的 per-host gauge remove）。
- **把 `usb_probe_unavailable` 也做成 reason**：弃——缺权限的 host 会集体 DEGRADED，
  与 issue 的「观察窗 0 误报」验收冲突；改为一次性 warning + 文档写明部署前置与自检命令。
- **Agent 自动 unbind/rebind**：弃——issue 的「不做什么」明确：打断面大，先告警到人。
- **把 `usb_fault_reasons` 并入 `usb_device_count` 的口径**：弃——两者正交（一例见证于
  三台失明主机的 USB 计数全为 0），合并会让「为什么 DEGRADED」不可归因。

## Verification

- **离线回放**（issue 验收项）：`backend/agent/tests/test_kernel_usb_faults.py` 用三例现场的
  逐字日志行（xhci 三行定式、`error -71` / `-110`、`Maybe the USB cable is bad?`）跑解析与判据。
- **变异测试（6 处，逐条按预期红）**：A 去掉「零设备」合取 → `test_dead_but_devices_visible_is_not_blind`
  等 2 条红；B 不清 latch → `test_devices_returning_clears_latch` 红；C 扫描改同步 →
  `test_poll_never_blocks_on_slow_scan` 红（实测阻塞 5.2s）；D capacity 丢弃 reasons → 2 条红；
  E 心跳不传 `usb_fault_reasons` → 接线守卫红；F 去掉权限提示判定 →
  `test_permission_hint_is_unknown_not_clean` 红。恢复后全绿。
- **实跑扫描**（本机，只读）：`scan_kernel_usb_faults()` 的 `--boot` 与 `--since @epoch` 两种
  argv 均 rc=0；本机因权限提示返回 `None`（未知）而非「零故障」——正是上面那条实测发现。
- `env -i PATH=… PYTHONPATH=. python -m pytest backend/agent/tests/ -q` → **2090 passed**
- `python scripts/run_gates.py check:quick` → **[OK] check:quick (12 gates)**
- 文档同步：`host-device-visibility-triage.md` §3（新增「L1 判据已落地」与两条缺口）、
  8.87 事故复盘 §5 的监控项标记为已落地（并注明仍不自动 unbind/rebind）。

## Revisit

- **部署前置（最重要）**：Agent 运行用户必须在 `systemd-journal`（或 `adm`）组，否则探测
  静默降级为「未知」（一条 warning，无 reason）。逐个工位自检：`journalctl -k -n1` 能出
  内核行即可。若 fleet 不能加组，出口是把「只读内核日志尾部」加入
  `backend/agent/stp_agent_priv.py` 的白名单命令（沿用既有提权模式）——本单不做。
- **告警通道**：接 #2880（平台 `rule_files` 挂载）之后补平台规则
  `usb_host_controller_dead` → 告警；届时本单的 reason 即为指标源。
- **阈值标定**：20 条/小时与 cable ≥5 是首版估计（依据 .102 的 400~3700 行/天），上线后按
  真实分布校准；出现误报时先确认不是「权限缺失被读成干净」。
- `#2902`（`total_devices == 0` 时门禁失效、L1–L4 不可区分）与本单正交：本单只补
  「有内核证据的失明」，打闸/禁调口径仍归那条。
