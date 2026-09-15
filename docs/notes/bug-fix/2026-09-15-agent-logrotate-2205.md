# #2205 agent 进程日志无轮转：logrotate 配置随 Ansible 链下发（copytruncate）

Status: implemented
Class: bug-fix

## Decision

agent 进程日志（`stability-test-agent.service` 的 `StandardError=append:.../logs/agent_error.log`）
此前**无任何轮转**——`log_archiver` 只管 SSD `run_log_dir`、`local_disk_monitor` 只管
HDD spill，进程日志在两条治理链之间「两头不靠」。2026-09-15 全队实测 **48/48 台合计
94.5GB**、最大单台 4.89GB、≈54MB/天/台。

修复 = 经 `update_agent.yml`（agent_deploy 链）下发两件套：

1. **`Ensure logrotate is installed (#2205)`**：`ansible.builtin.apt`（`logrotate`，
   `cache_valid_time: 3600`）——canary 实测主机**未预装**（`logrotate: not found`），
   装包后随 Debian `cron.daily` 的 logrotate 定时器生效；
2. **`Configure agent log rotation (#2205)`**：渲染 `/etc/logrotate.d/stp-agent`
   （root:root 0644）——两文件同块（`agent_error.log` + `agent.log`），
   `daily` + `size {{ agent_logrotate_size }}`（defaults `200M`）+
   `rotate {{ agent_logrotate_rotate }}`（defaults 7）+ `compress` + `delaycompress` +
   **`copytruncate`** + `missingok` + `notifempty`。

**`copytruncate` 是本修复的机制关键**：systemd 以 `append:` 打开日志文件、**持有 fd**；
logrotate 默认 `create` 模式（rename + 新建）不会让 systemd 重新打开——它会继续写
已改名的旧文件，轮转**静默失效**。`copytruncate`（copy 到 .1 + 截断原文件）保 fd 有效。
契约测试以正则钉住「有 copytruncate、无独立 create token」（`create` 是
`copytruncate` 的子串，不能按子串断言）。

**只配置、不主动执行轮转**：首轮由 logrotate 定时器按 size/daily 自然触发（幂等部署
每次只更新配置，不产生额外 IO/截断）。存量峰值代价：首轮 copytruncate 时瞬时约
2×（1.68GB 级）+ 随后压缩（7 份，长期 ~GB 级/台），当前磁盘余量充足（`/` 226G 用 2%）。

## Alternatives

- **`StandardError=journal`（交 journald）**：自带 `SystemMaxUse` 轮转、更干净；但改变
  日志访问方式（`journalctl -u`）与既有运维文档/习惯（`logs/agent_error.log` 是排障
  入口，含本次 #2083 验证的 grep 用法），本单不动观测面。留作 Revisit。
- **应用侧 `RotatingFileHandler`**：需改 agent 代码 + 处理与 systemd 的重定向关系
  （进程 stdout/stderr 已被 systemd 接管），改动面反而更大。否决。
- **纯 `daily`（不带 size）**：日志量随机队/设备数波动，纯每日轮转在高产主机上单文件
  仍可达 GB 级；`daily + size 200M` 取二者更早触发。否决。
- **本 PR 顺手 truncate 存量 5 台 >4GB 文件**：不可逆丢历史，且首轮轮转（部署后）会
  自然压缩保留 7 份——交给部署后的自然轮转，不在代码单里做破坏性动作。否决。

## Verification

- `python -m pytest tests/test_ansible_logrotate_2205.py -q` → **6 passed**（task 存在性、
  copytruncate 无 create、路径经变量渲染、参数来自 defaults、策略 token、apt 先于配置）；
- 同族回归：`tests/test_ansible_digest_contract.py` + `tests/test_ansible_resources_guard_2166.py`
  → 13 passed（合计 18 passed）；
- `ansible-playbook playbooks/update_agent.yml --syntax-check` → 通过；
- **canary 真机 dry-run（172.21.15.59，`logrotate -d` 不改文件）**：
  ```
  considering log .../agent_error.log → log needs rotating      （1.68GB > 200M）
  considering log .../agent.log       → log does not need rotating（0 字节，notifempty 语义）
  rotating log ..., log->rotateCount is 7  ...
  copying .../agent_error.log to .../agent_error.log.1
  truncating .../agent_error.log                                （copytruncate 路径确认）
  ```
  同批已在 canary 安装 `logrotate 3.22.0`（装包步骤的真机实证）。

## Revisit

- 铺开后复扫（同 #2205 issue 的 `stat` 命令）：总量应 <10GB 且受 200M×7 约束；
- 若 journal 化（`StandardError=journal`）另行裁决——本单不动观测面；
- 首轮轮转后抽查 1-2 台的产物链（`.1` 未压缩 → 次轮 `.1.gz`、`.7.gz` 上限）与
  systemd fd 语义（agent 重启后继续写入新文件）。
