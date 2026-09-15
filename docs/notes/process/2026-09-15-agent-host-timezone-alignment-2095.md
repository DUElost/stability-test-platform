# 全机群 Agent 主机时区对齐（#2095）

Status: implemented
Class: process

## Decision

新增 `tools/ansible/playbooks/set_timezone.yml`，把全部 Agent 主机的时区统一为控制面约定的
`Asia/Shanghai`（+0800），并已在机群 48 台上执行完毕。

起因：主机装机镜像默认时区为 `America/Los_Angeles`（PDT，−0700），与控制面 `Asia/Shanghai`
（+0800）挂钟相差 **15 小时** —— 主机侧日志时间戳无法与平台时间线直接对照，排查真机问题时极易
误判（本次排查 #2010 时即被绕入，一度误以为是时钟走偏）。

定性：这是**时区配置**问题，不是时钟问题 —— 双方 UTC 仅差 1 秒，且主机
`System clock synchronized: yes`、`NTP service: active`、`RTC time` 亦为 UTC：

| | 本地时间 | UTC | 时区 |
|---|---|---|---|
| 控制面 | 10:50:36 CST (+0800) | 02:50:36 | `Asia/Shanghai` |
| Agent 主机 | 19:50:37 PDT (−0700) | **02:50:37** | `America/Los_Angeles` |

playbook 的关键设计：

1. 改时区**之前**断言本机 UTC 与控制面偏差 ≤ 5s；超差直接失败 —— **不把"时钟真的走偏"
   用改时区掩盖**；
2. 幂等：已是目标时区时为 no-op；只执行 `timedatectl set-timezone`，不触碰 NTP / 系统时钟 /
   硬件时钟；
3. 长驻进程（Agent 自身）不会因 `/etc/localtime` 变化而重读时区，故提供默认关闭的
   `-e tz_restart_agent=true`（前提：该机无活跃作业，与平台侧 ADR-0021 软锁同理）。

**已接入装机流程**：`install_agent.yml` 顶部 `import_playbook: set_timezone.yml`，刻意置于安装 play
**之前** —— 装机脚本末尾会重启 Agent 服务，新进程届时直接采用新时区，无需额外重启。

## Alternatives

- **不改，仅改善日志格式**：只让 Agent 日志输出 UTC/带偏移即可消除"对照困难"。未选：主机侧
  其他产物（脚本本地时间、第三方工具文件名）仍与本机时区耦合，且运维在主机上直接看
  `date`/`ls -l` 时依旧困惑；统一时区才是根治。
- **全机群统一为 UTC，由平台渲染呈现**：工程上更"标准"。未选：与既有平台约定
  （`STP_TIMEZONE` 默认 `Asia/Shanghai`、`aee/paths.py` 显式 Shanghai stamp）冲突，改动面更大；
  本次按用户明确要求"与控制面一致"执行。
- **逐台 SSH 手工 `timedatectl`**：未选：48 台不可审计、不可幂等、无法预演；改用仓库既有
  Ansible 体系（同 `install/update/service_agent.yml`）。
- **顺带修改脚本内本地时间用法**（如 `powercycle_*` 的 `run_id`）：未选：这些是**版本化不可变
  脚本**，就地改会被 `check-script-version-immutability` 拒绝，须出新版本，属另一件事。

## Verification

- 预演（`--check`）先跑，再金丝雀（`172.21.x.56`）单台，最后全机群；`--syntax-check` 通过；
- 幂等：金丝雀复跑 `changed=0`，输出 `Asia/Shanghai (+0800) -> Asia/Shanghai (+0800) [already aligned]`；
- 机群复核（独立于 playbook 自身断言）：`date +%z` → **48/48 `+0800`**，无例外；
- 重启前先确认**全机群活跃作业 = 0**；重启后 `systemctl is-active stability-test-agent` →
  **48/48 `active`**；
- 抽查 3 台日志时间戳已与平台一致（例：同一时刻 UTC `02:58:42`，Agent 日志写 `10:58:38`）；
- 仓库门禁：`check-internal-ip-leak.py --check` 通过（本 note 内主机地址均作泛化）；
  `check_governance_surface.py` 对新增 Note 的四节契约通过。

## Revisit

- **新建主机已纳入**：`install_agent.yml` 顶部已 `import_playbook: set_timezone.yml`
  （置于安装 play 之前，见下）。若改用非 `linux_hosts` 的 inventory，需 `-e tz_hosts=<group>`。
- **脚本类本地时间**：`powercycle_*` 的 `run_id`、vendored `aimonkey` 日志文件名仍取本机时间；
  如需修正应发新脚本版本。
- **口径再议**：若后续决定"全机群 UTC + 平台渲染"，本 playbook 的 `tz_target` 与断言偏移需同步调整。
- **回滚**：对目标主机 `timedatectl set-timezone America/Los_Angeles`（playbook 每次打印
  `old -> new` 便于取回原值），Agent 侧无需额外动作。
