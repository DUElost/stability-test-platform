# 0 行内核日志不许被读成「读过且干净」：正向可读性探针 + locale 钉死（#2957）

Status: implemented
Class: bug-fix

## Decision

昨天我在 #2957 断言「#2911 的内核日志通道在 fleet 上恒不可读」，证据是
`install_agent.sh` 的 `User=android` ＋ 本机用 `-n 3` / `--since -1h` 复现出的权限提示串。
**结论对了，机制错了**，而且错的部分正好是判据自己：

```
$ journalctl -k --no-pager -o cat --boot      # 非特权，本模块真实使用的 argv
rc=0  stdout=0 字节  stderr=0 字节            # ← 连那条权限提示都不打印
$ sudo journalctl -k --no-pager -o cat --boot | wc -l
373600                                        # ← 真读得到时的形状（同机对照）
```

提示串只在**别的** argv 形状里出现 ⇒ `_BLIND_HINT_MARKERS` 在真实路径上永不命中 ⇒
`parse_kernel_usb_faults([])` 返回一个 `lines=0` 的"干净"结果 ⇒ #2911 的「未知 ≠ 干净」
守卫不生效，**并且把我 #2966 的 `usb_kernel_log` 盖章成 `ok`**。现网实测：36/36 台已升级
host 全报 `ok`、`unavailable=0`，fleet 级失明告警恒绿。也就是说，我上一轮为了"不让假绿骗人"
而建的通道指标，自己产出了一个假绿——是它当天就把矛盾量出来的（`ok=36 / unavailable=0`
与我公布的"48/48 全黑"相反），这一点值得记下来：**指标与推断冲突时，改判据、也改自己的结论。**

修法（只加判据，不改 #2911 的扫描形状与游标语义）：

1. **正向可读性探针**：任何一次扫描折出 0 行时，问一句「整段 boot 里有没有哪怕一行内核日志」
   （`journalctl -k --no-pager -o cat --boot --lines=1`；`--lines=1` 让成本与日志总量无关）。
   取不到一行 → 返回 `None`（未知），通道态 → `unavailable`。
2. **探针只在 0 行时跑**：否则每拍多一次 fork，且把"读到 1 行"的常态也拖去问一遍。
3. **子进程钉 `LC_ALL=C` / `LANG=C`**：systemd 的消息是翻译过的（本机 `locale -a` 有 `zh_CN`
   且 `/usr/share/locale/zh_CN/LC_MESSAGES/systemd.mo` 存在），按文本匹配提示串的判据不能
   依赖语言环境——否则中文 host 上匹配静默失效，又是一个假绿。

## Alternatives

- **「stderr 为空即未知」**：错。静默窗（真读到、恰好没有新内核日志）同样 stderr 空 ⇒
  会把整个 fleet 判成不可读，用一个假红换一个假绿。必须有**正向**证据。
- **每次扫描都读整段 boot**（放弃增量游标）：373k 行 × 每 60s 的重复读不划算，还会丢掉
  #2911「游标先推进再扫描，宁可重复不可漏读」的既有语义。
- **加第四个通道态 `empty`**：能表达，但词表要在 agent 常量、控制面分桶、告警分子三处各扩一次，
  而它的运维动作与 `unavailable` 一模一样（都是"这台机没读到内核日志"）⇒ 合并成一个事实，
  不留第二真值。将来若真要区分「权限没给」与「journald 没跑」再拆。
- **顺手给 agent 加 `systemd-journal` 组**（#2957 选项 A）：权限扩张 + 48 台重跑安装步骤，
  属 ops 决策，不由写判据的人单方签字。本单只负责让"读不到"变成**可证明**的事。

## Verification

- **本机同机前后对照**（uid 1000，groups 不含 adm/systemd-journal；同机 sudo 对照 373,600 行）：
  修复前 `parse(stdout)` → `lines=0` 被上层解释为 `ok`；修复后
  `kernel_log_is_readable() is False`、`scan_kernel_usb_faults(since=None) is None`、
  增量形状 `since=<ts>` 同样 `None`。
- 新增 `TestReadabilityProbe` 6 条：真形状→未知、**静默窗仍算可读**（反向钉子，防假红）、
  有日志时不再跑探针（成本钉子）、每个 journalctl 调用都钉 locale、探针的三种失败形状、
  以及端到端一条（真扫描器 + 真 argv ⇒ `channel_state()` 必须 `unavailable`）。
- `pytest backend/agent/tests/test_kernel_usb_faults.py backend/agent/tests/test_capacity_reporter.py`
  → `73 passed`；`ruff check` 干净；`check_governance_surface --check` 全绿；
  `check-internal-ip-leak` 3673 文件通过；`run_gates check:quick` → 见 PR。
- **变异自证 4 条**：M1 去掉探针 → `2 failed`；M2 探针无条件跑 → `1 failed`；
  M3a 扫描侧不钉 locale → `1 failed`；M3b 探针侧不钉 locale → `1 failed`。
  **M3 的第一版是假绿**：断言写成"检查 `calls[0]` 的 env"，而变异恰好命中的是探针那处调用
  ⇒ 全绿。改成"对**每一个** journalctl 调用断言 env"后两个位点都被抓住。这与本单主 bug
  同源：**桩与断言的形状必须是被测调用的形状**——`#2911` 的 `test_permission_hint_is_unknown_not_clean`
  喂的是 `-n 3` 形状的 stderr，而生产走 `--boot`，所以它绿着放过了一个恒失效的守卫。
- 现网只读数据（中心 Prometheus）：`count(stability_host_health_reason)=528`（48 host × 11 桶）、
  `count(stability_host_kernel_log_channel)=144`（48 × 3）、`ok=36 / unknown=12 / unavailable=0`；
  12 台 `unknown` 与 `agent_code_revision=17cef158`（未升级到含本字段的构建）**完全对应**，
  36 台 `ok` 对应 `22715aad` ⇒ 分桶与 rollout 口径成立。

**pending**：

- [ ] 上线后需复核一次：**预期** 36 台从 `ok` → `unavailable`，`StabilityUsbKernelLogChannelDark`
      在 6h 后转红（过半且 ≥5 台）——那是真信号，不是误报；若反而保持 `ok`，说明部分机群
      已另行加组，#2957 的"恒不可读"前提要按 host 拆细。**两种结果都要回填，别只看绿灯。**
- [ ] 规则进生产装载面仍需人工同步（`docs/operations/README.md` §6）。
- [ ] 本单不改变「读不到」这件事本身；要它变绿只能走 #2957 的 A/B/C。

## Revisit

- 若 #2957 选 A/C（授权落地）：探针自动放行、告警自行回落，本单无需回滚任何东西。
- 若将来需要区分「权限未授」「journald 未运行」「journal  volatile」三种黑：再加第四态，
  并同时扩 agent 常量 / 控制面桶 / 告警分子三处——**必须一起扩**，否则又造出一个恒绿桶。
- `docs/operations/host-device-visibility-triage.md` 的 L1 段已按实测改写；若 argv 形状再变
  （例如改用 `--since` 单形态），`TestReadabilityProbe` 的"真形状"用例是唯一能抓住回归的地方，
  别把它退化成"喂一个假 stderr"。
