# boot 全量计数不再冒充「最近一小时」：usb_link_degraded 的量纲收口（#2978）

Status: implemented
Class: bug-fix

## Decision

`KernelUsbWatch` 首扫走 `journalctl -k --boot`（覆盖**整段 boot**，可能是 3 天），但它的
`link_errors` / `cable_suspect` 计数却按**本次扫描时刻**塞进 3600s 窗当增量样本用。后果：

- `usb_link_degraded` 可由一段与告警文案不符的区间支撑——`StabilityHostUsbLinkDegraded`
  （`for: 45m`，文案写「最近一小时…超阈」）单条 boot 样本就能撑满整窗；
- agent 热更新 / 服务重启每次都会重跑 boot 首扫 ⇒ 一台当前干净、只是本 boot 早期有过插拔
  风暴的 host 会被拉成 DEGRADED 最长 1 小时。`.102` 的量级参考（400~3700 行/天 ≈ 17~154/小时）
  说明**单段 boot 的累计轻易越过阈值 20**，这不是理论风险。

立一条不变量并让它只有一处实现：**一个样本只能声明它所覆盖区间的计数**。

1. `since is None`（boot 扫描）：只取**布尔**事实（`hc_dead` latch——覆盖「死亡发生在本次
   进程之前」，这是首扫存在的理由，也是 #2911 做对的部分）；**计数不入窗**。
2. 不入窗 ≠ 丢掉：留一条 `kernel_usb_boot_counts_not_windowed` INFO 供取证。真在风暴时运维
   仍能在 agent 日志里看到量级，只是它不再驱动 reason/告警。
3. 增量样本的时间戳改取**区间起点**（= 上次游标），不是扫描时刻——同一条规则的另一半，
   顺带去掉样本比其区间"年轻"一个扫描周期（60s）的偏差。

## Alternatives

- **给 boot 样本打「boot 起点」时间戳**（`/proc/uptime` 反推）：能保住「开机 <1h 且正在风暴」
  这一小段即时检测力，但为窄场景引入一次新的系统读取与一个失败模式；增量窗 ≤60s 后本来就会
  重新覆盖到该 host。选简单且不虚报的那条。
- **首扫也只读 `--since` 一个窗**：会丢掉「开机即死 / agent 后起」的 boot 全量证据，那正是
  #2911 首扫的设计目的。否决。
- **把阈值抬到 boot 累计量级**（比如 200）：拿量纲不同的两件事凑一个数，等于把真风暴也一起
  调哑。否决。
- **只改告警文案**（把"最近一小时"改成"boot 内累计或最近一小时"）：文案迁就 bug，两个语义
  挤在同一个 reason 里——`hc_dead` 要的是"曾经死过 ∧ 现在看不见"，`link_degraded` 要的是
  "现在正在劣化"，合并就再也分不开了。否决。

## Verification

- 新增 `TestBootCountsNotWindowed` 5 条：issue 的复现形状（首扫 100、之后 0 ⇒ 全程不报，
  含窗尾 t=3599）、反向钉子（增量窗真出 100 条 ⇒ 照报，防"修成漏报"）、`hc_dead` latch
  仍吃 boot 证据（防把正确的部分一起砍掉）、时间戳=区间起点、取证 INFO 仍在。
- **改了一条既有断言**：`test_chronic_errors_accumulate_in_window` 原本第 3 轮就断言出 reason，
  那是**在给这个 bug 背书**（第 1 个样本正是 boot 全量）。修后到阈值需 4 轮（3 个增量样本
  21≥20），用例注释里写明了改动原因，不靠"看起来更长了"糊过去。
- 告警文案与场景文件同步改（promtool 逐字比对）：`promtool test rules` → `SUCCESS`。
- `pytest backend/agent/tests/test_kernel_usb_faults.py` → `42 passed`
- **变异自证**（每次改完即还原复跑）：
  - M1 **还原修复前形状**（boot 计数按扫描时刻入窗）→ `4 failed`，红在复现钉子 + 被改过的
    累积用例 + 时间戳 + 取证四条
  - M2 只删取证日志（不入窗也不留痕）→ `1 failed`（专为其准备的那条）
  - M3 时间戳退回扫描时刻 → `1 failed`
  - M1 的第一版是**不真实的变异**（把条件改成 `if False`，导致 `None` 进排序比较直接抛错，
    `11 failed`）——它证明不了任何语义，已换成"退回修复前实现"重做，数字如上
- `pytest backend/agent/tests -q` → `2182 passed`（全 agent 套件，无回归）
- `pytest tests/ -q --deselect tests/test_prometheus_alerts_contract.py` 与 `run_gates check:quick`
  → `1681 passed, 39 deselected` / `[OK] check:quick (12 gates)`；`ruff` 干净、
  `check-internal-ip-leak` 3684 文件通过
- **rebase 到含 #2970（场景层新轴）的 main 后复跑**（两侧都改过 `alerts-stability-platform.test.yml`，
  旧结果不可沿用）：`tests/test_prometheus_alerts_contract.py` → `40 passed in 352.94s`；
  `promtool test rules` → `SUCCESS`；本文件用例 → `42 passed`

**pending**

- [ ] 现网是否已经在误报未取证（需生产 host 的 boot 内 USB 错误分布）。本单主张的是
      「量纲不一致 + 机制可复现」，与 #2978 正文同口径；#2957 的可读性探针合并后
      （`unavailable` 会盖住这条路径）此误报在现网更不可能出现，但不能拿它当修复依据。
- [ ] 规则上线仍需人工同步中心副本（`docs/operations/README.md` §6）。

## Revisit

- 若将来真要"boot 内累计劣化"这条判据（例如装机即坏线），那应是**第四个 reason** +
  独立窗口/阈值，而不是复用 `usb_link_degraded` 把两种时间语义混进一个词表值。
- 若 `LINK_WINDOW_SECONDS` 与 `SCAN_INTERVAL_SECONDS` 的比例被改动，`test_sample_is_stamped_at_interval_start`
  与累积用例的时间假设要一起看（它们钉的是"一个样本 = 一个区间"这条规则，不是某个数）。
