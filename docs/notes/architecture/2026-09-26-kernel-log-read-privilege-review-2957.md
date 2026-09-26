# 内核日志读权限：安全评审材料（#2957）

Status: proposed
Class: architecture

## Decision

**待评审的推荐方案：给提权 wrapper `stp-agent-priv` 增加一个只读子命令 `read-kernel-log`**，由它以 root 执行固定形态的
`journalctl -k`，Agent 经已有的 NOPASSWD wrapper 入口调用。不给 Agent 用户加 `systemd-journal` / `adm` 组，也不新增单独的
`journalctl` sudoers 行。本文是 owner 与安全评审人裁决 #2957 A/B/C 的输入，评审通过前不实施。

### 要解决的问题

- Agent 以 `User=android` 运行，内核日志在机队上恒不可读：`dmesg_restrict=1`、`/dev/kmsg` 非特权打开即 EPERM、非特权 `journalctl -k` 返回空（#2957 正文）。
- #3011 的探针上线后，46/46 台已升级主机的 `usb_kernel_log` 通道报 `unavailable`，`StabilityHostUsbControllerDead` 与 `StabilityHostUsbLinkDegraded` 两条内核证据类规则因此**恒绿**，但绿不代表已覆盖。
- 唯一有效的零权限信号是 `usb_tree_empty`，它只能说「整机看不见」，说不出是主控死亡还是线被拔。

### 推荐方案（C 的收窄形态）

新增 wrapper 子命令：

```
stp-agent-priv read-kernel-log (--boot | --since-epoch <int>) [--lines <int>]
```

wrapper 内部执行的命令**固定**为：

```
/usr/bin/journalctl -k --no-pager -o cat  +  (--boot | --since @<epoch>)  +  [--lines=<n>]
```

约束（全部在 wrapper 内校验，违反即 exit 2 `STP_AGENT_PRIV_ERROR`）：

1. `--since-epoch` 必须是非负整数且不晚于当前时间；`--lines` 是 1–5000 的整数；二者之外不接受任何参数。
2. 不透传任何路径、单元名、匹配表达式：禁止 `--file` / `-D` / `--directory` / `-M` / `-u` / `--grep` 等所有其它 journalctl 选项。
3. 子进程环境清空后只设 `LC_ALL=C`、`PATH=/usr/bin:/bin`，不设 `SYSTEMD_PAGER`（配合 `--no-pager`）；超时 30 秒；stdout 截断上限 8 MiB。
4. 解析仍在 Agent 侧（`kernel_usb_faults.py`）；wrapper 只搬运原始输出，不做过滤判定。
5. `capabilities` 输出增加该子命令名，`selftest` 的 parser↔契约表同步。

Agent 侧改动：`kernel_usb_faults.py` 的扫描与可读性探针在 `capabilities` 含 `read-kernel-log` 时改走
`sudo -n /usr/local/sbin/stp-agent-priv read-kernel-log …`；不含时维持现状（非特权调用，结果仍判 `unavailable`）。

### 三条路的提权面对比

| 维度 | A 加 `systemd-journal` 组 | B 放弃内核日志通道 | C（推荐，wrapper 子命令） |
|---|---|---|---|
| 可读范围 | 整机全部 journal（所有单元、所有用户条目） | 无 | 仅内核消息（`-k`） |
| 参数面 | 无约束，任意 journalctl 选项 | 无 | 两个整数参数，wrapper 校验 |
| 与 ADR-0037 不变量 | 绕开 wrapper，新增一条组权限维度 | 无影响 | 保持「NOPASSWD 只授 wrapper」 |
| 生效方式 | 每台主机重跑安装（组归属不随热更新） | 无 | 每台主机更新 wrapper（`update_agent.yml`，同样不随热更新） |
| 回滚 | 逐台移出组并重启服务 | 无 | 删除子命令；Agent 自动回落到现状 |
| 能力收益 | 两条内核证据类规则有数据 | 两条规则永久无数据，L1 失明检测只剩 `usb_tree_empty` | 同 A |

### 信息暴露评估

`journalctl -k` 的输出是内核环形缓冲：驱动与 USB 枚举消息（含设备序列号、VID/PID）、OOM、文件系统错误等。
不含其它进程的应用日志或用户会话内容。这些信息本来就会经 `usb_tree_empty` / 心跳上报的形式部分进入平台。
A 方案会额外暴露所有单元的应用日志（可能含口令、token 的误打印），这是推荐 C 而非 A 的主要理由。

### 交付与灰度

- wrapper 不随热更新下发（`host_updater.py:201-215` 的能力判据）；需要逐台跑 `tools/ansible/playbooks/update_agent.yml`，或随下一次重装。
- 灰度：1 台 → 5 台 → 全量；每级观察 `stability_host_kernel_log_channel{state}` 从 `unavailable` 转 `ok`、`StabilityUsbKernelLogChannelDark` 回落。
- 未更新 wrapper 的主机行为不变（`capabilities` 不含新子命令 → 维持非特权调用），可以混合运行。

### 给评审人的核对清单

- [ ] 参数校验是否能被绕过：负数、超大整数、科学计数法、带空格或 `=` 的拼接、重复参数。
- [ ] `sudo` 的 `env_reset` 与 wrapper 自身的环境清空是否足以防止 `PAGER` / `LESS` / `SYSTEMD_*` 注入。
- [ ] 输出截断与超时是否会让一次读取拖住心跳线程（扫描本就在后台线程，#2900）。
- [ ] 内核消息中的设备序列号经平台上报后的可见范围是否可接受。
- [ ] 回滚路径：删除子命令后 Agent 是否确实回落而不报错。

## Alternatives

- **A 加组**：实现最省，但把 Agent 可读范围从「无」一步扩到「整机全部日志」，且绕开 ADR-0037 收敛下来的唯一提权入口。
- **B 放弃内核日志通道**：零权限变化，但两条内核证据类规则永久无数据；「主控死亡」与「线缆被拔」在平台侧永远不可区分，现场处置只能靠人上机。
- **单独一条 `journalctl -k -o cat` 的 sudoers 行**（#2957 原文的 C）：比 A 窄，但 sudoers 无法约束 `--since` 等参数的取值，且在 wrapper 之外新增 NOPASSWD 面，与 ADR-0037 的收敛方向相反。

## Verification

- 读码锚点（`main@8dc9d82`）：`backend/agent/stp_agent_priv.py:1-35`（子命令契约与不变量）、`:562-583`（sudoers 只授 wrapper）；
  `backend/agent/install_agent.sh:182-200`（wrapper 安装与 bootstrap）；`backend/services/host_updater.py:201-215`、`:264-270`（热更新不携带 wrapper，缺能力即指引 `update_agent.yml`）；
  `tools/ansible/playbooks/update_agent.yml:202-221`（wrapper 下发与 selftest）；`backend/agent/kernel_usb_faults.py:54-101`（探针与扫描 argv）。
- 现网状态引自 #2957 评论（2026-09-21）：46/46 台已升级主机通道 `unavailable`。
- 本文未改任何代码与主机配置。

## Revisit

- 评审通过 → 以本文为 #2957 的实施规格领单；评审不通过 → 选 B，并在 `docs/operations/host-device-visibility-triage.md` 的 L1 层写明「本机队内核日志判据不覆盖」。
- #2983（控制面 SSH 只读探针）落地后，复核两条通道是否需要都保留。
