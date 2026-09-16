# #2218 agent 配置面通道：`agent_config` 组 + `configure_agents.yml`（与更新面分离）

Status: implemented
Class: feature

## Decision

**问题**（#2205 铺开暴露）：`update_agent.yml` 的 `hosts: linux_hosts` 只覆盖
Ansible 正式面 **14/48 台**；34 台不在该面、又无「主机配置类变更」（apt、/etc、
systemd unit）的正式下发通道——logrotate 铺开只能 ad-hoc 绕过（无幂等/审计/测试）。

**裁决**（#2218 评论，2026-09-15，用户裁决）：① 扩面 Ansible（修正调研后成本极低：
ADR-0037 提权边界 48/48 已完成、wrapper/sudoers 零改动）；② **新建 `agent_config`
配置面组（48 台）**，`linux_hosts`（14 台）保持「agent 版本更新」语义——34 台 agent
代码更新**仍走热更新**，仅配置类变更走新组；③ 控制面系统下发与 ad-hoc 常态化否决。

**实现（四点）**：

1. **`configure_agents.yml`（新）**：`hosts: agent_config`、`serial: "20%"` +
   fail-fast（与更新面同款）；**只做配置类变更**（范围隔离由
   `tests/test_ansible_config_channel_2218.py` 的禁词表锚定：不得出现
   rsync/synchronize/systemctl restart/hot-update/update_agent.yml 等）；
2. **logrotate task 抽共享文件**（`roles/agent_deploy/tasks/logrotate.yml`）：
   `update_agent.yml` 与 `configure_agents.yml` 共同 include（单一维护点）；
3. **跨面变量上移 `group_vars/all.yml`**：`ansible_become*`、`agent_install_dir`、
   `agent_logrotate_*`——group_vars 按组名匹配，新组原先读不到 linux_hosts.yml
   的共享项；上移后单一定义（`linux_hosts.yml` 不重复定义，测试锚定）；
4. **tag 显式写在共享 task 上**（关键坑，见 Verification）。

**组结构**（`inventory.ini`，本地文件、凭据不入 git）：

```text
[agent_legacy]        # 34 台（原不在 Ansible 正式面；凭据取自 hosts.ini 的 vars 段）
[agent_config:children]
linux_hosts           # 14 台（现有）
agent_legacy          # 34 台
```

## Alternatives

- **34 台并入 agent_prod**（放弃）：`update_agent.yml` 全量流程随之可跑全队——34 台
  更新方式可能从热更新切到 Ansible（部署语义变化，超出本单范围）。新建配置面组
  精准覆盖需求且不动既有语义。
- **控制面 host_updater 扩展系统面下发**（否决）：ADR-0037 提权边界已完成、
  Ansible 面现成——不必要的新面。
- **ad-hoc 常态化**（否决）：无幂等/审计/测试锚/失败重试。
- **共享 task 用 copy 模板（template 模块）替代**（否决）：当前无动态字段需求，
  `content:` 内联 + 变量渲染已够；引入模板文件增加面。

## Verification

- `pytest tests/test_ansible_logrotate_2205.py tests/test_ansible_config_channel_2218.py
  tests/test_ansible_digest_contract.py tests/test_ansible_resources_guard_2166.py -q`
  → **25 passed**；
- 双 playbook `--syntax-check` → 通过；
- **双通道 dry-run + 真跑**（2026-09-16）：
  - 配置面 `configure_agents.yml --tags logrotate --limit 172.21.x.x`（34 台组）→
    **`ok=3 changed=1`**（included 两 task 实际执行、配置下发）；
  - 更新面 `update_agent.yml --tags logrotate --limit 172.21.x.x`（14 台组）→ 同构通过；
- **关键坑（实测）**：`include_tasks` 的 `tags` 在 ansible-core 2.19 **不传播**到
  included tasks——`--tags logrotate` 只跑 include 本身（**`ok=1`、included tasks
  被静默跳过**）；把 tag 显式写在共享 task 的各 task 上后恢复（`ok=3`）。
  测试加 `test_logrotate_tasks_carry_tag_explicitly` 反向钉住（不得依赖传播）。
- **首验：全队重下发**（2026-09-16，`agent_config` 48 台）——本通道的首个端到端真跑，
  以 logrotate 配置的全队重下发承接（`serial: 20%` → 5 批；`any_errors_fatal` 未触发）：
  - 预演 `ansible-playbook playbooks/configure_agents.yml --tags logrotate --check --diff`
    → 48/48 可达、`failed=0`；
  - 真跑（同命令去 `--check`）→ **48 台 `ok=3`、`changed=46`、`failed=0`、`unreachable=0`**
    （`exit=0`）；逐台 diff 只有注释头一行（`# 由部署链下发（#2205）` →
    `# 由 Ansible 下发（#2205 / #2218）`），其余字节与 ad-hoc 版完全相同——34 台 legacy
    的 ad-hoc 内容与正式通道内容同源，由此得实证；
  - **幂等**：紧接着复跑 → 48 台 `changed=0`（copy 内容比对全等）；
  - `changed=0` 的 2 台（canary 与 legacy 各 1 台，即开发期冒烟真跑覆盖的那两台），
    其余 46 台由本次首验覆盖；
  - 只读附带核验：48/48 台 `logrotate` 早已安装（apt task 恒 `ok`，无安装动作）；
    `/etc/sudoers.d/android` 全队不存在、`/etc/sudoers` + `/etc/sudoers.d/*` 内无
    `NOPASSWD … ALL` 宽面 → #2218 验收判据第 3 条（ADR-0037 联动）实测已达成
    （issue 正文「legacy sudo 宽面仍在」为过期描述）；
  - 原始日志（本机临时、非持久证据）：`/tmp/stp-2218-{check,run,rerun,sudoers}.log`。
- **故障注入：失败可见性**（2026-09-16）——首验全程无失败，失败路径改由注入补测：
  `--limit` 5 台 legacy + `-e ansible_become_password=<invalid>`（extra vars 优先级最高，
  失败发生在提权处，**对主机零写入**）→ `fatal: … Incorrect sudo password`（带
  `Origin: roles/agent_deploy/tasks/logrotate.yml:11`）、`NO MORE HOSTS LEFT`、
  **`exit=2`**；`PLAY RECAP` 只有 1 台——`serial: 20%` 的后续 4 批**未被尝试**
  （`any_errors_fatal` + `max_fail_percentage: 0` 的**整 play 中止**语义得实证，非静默跳过）。
  对照组（同 5 台、不注入）→ `exit=0`、5 台 `ok=3 changed=0`（注入无残留）。
  原始日志（本机临时）：`/tmp/stp-2218-fault{,-control}.log`；同一结论留档于 #2218 评论。

## Revisit

- **首验已执行**（2026-09-16）：全队 48 台经本通道完成 logrotate 重下发，
  `changed=46` → 复跑 `changed=0`；**失败可见性经故障注入实测**（`exit=2` + 整 play
  中止 + 对照组无残留，见 Verification）——判据②三项（幂等 / 失败可见 / 契约回归）
  全部有实测锚；
- `inventory.ini` 的 `agent_legacy` 段为**本地维护**（凭据敏感、gitignore）——新增/
  退役主机时需同步；若出现维护漂移，考虑把「主机清单真源」上收到控制面（另议）；
- 34 台 agent 代码更新仍走热更新（本单不改变）；若未来统一为 Ansible 更新，
  需另立裁决（含发布梯度与回滚语义）。
