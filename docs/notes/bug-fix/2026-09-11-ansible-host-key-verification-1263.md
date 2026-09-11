# Ansible 主机密钥恢复严格校验（#1263 / R14-F17）

Status: implemented
Class: bug-fix

## Decision

本质问题：`tools/ansible/ansible.cfg:2` 关闭 `host_key_checking`，`update_agent.yml`
的 rsync 通道再叠加 `StrictHostKeyChecking=no` + `UserKnownHostsFile=/dev/null`——
两处都绕过主机身份校验，网络劫持下运维凭据与部署内容可被中间人；业务 Paramiko
通道的 RejectPolicy（#908）不覆盖 Ansible / rsync 通道。

修复 = **恢复默认严格校验 + 显式信任流程文档化**：

1. `ansible.cfg`：`host_key_checking = True`（显式写入，防止再次误关）；
2. `update_agent.yml`：`agent_rsync_ssh_command` 移除两个宽松选项，保留
   `ConnectTimeout` 与端口逻辑；
3. runbook §3 更新默认约定并新增「主机密钥信任模型」：首次连接前经带内可信渠道
   核对指纹后登记 known_hosts（未登记时 fail-closed 中止）；换钥时显式核对新指纹 +
   `ssh-keygen -R` 重登记 + 运维记录留痕。

不引入仓库内逃生阀（不做"默认严格 + 可配置放宽"的开关）——受控实验如需绕过由
执行者在其本机显式覆盖，不进入仓库配置。

## Alternatives

- **可配置变量（默认严格 + 显式放宽开关）**——放弃：验收要求"默认校验 + 换钥显式
  确认/审计"，开关会成为事实上的常用路径，弱化信任模型；
- **playbook 内指纹断言（inventory 维护指纹变量）**——放弃：`inventory.ini` 为本地
  敏感文件不入库，指纹单源的维护与轮换成本高于收益；known_hosts + 人工核对已是
  标准流程；
- **ssh-keyscan 自动登记（pre_task）**——放弃：keyscan 不防首次 MITM，自动登记会把
  "显式确认"变成形式；登记动作留在人工流程，文档明确核对要求；
- **只改 ansible.cfg、不动 rsync 通道**——放弃：rsync 独立复用 ssh，仍带两个宽松
  选项（本 issue 证据之一），必须同步移除。

## Verification

实际运行（worktree `/tmp/stp-1263`，基于 `origin/main`）：

- `pytest tests/test_ansible_host_key_verification.py tests/test_update_agent_playbook.py
  tests/test_ansible_agent_secret_source.py tests/test_install_agent_artifacts.py -v`
  → **25 passed**，含新增 3 例：cfg `getboolean("host_key_checking") is True`；
  rsync 命令不含 `StrictHostKeyChecking` / `UserKnownHostsFile` 且保留 `ConnectTimeout`；
  runbook 含首连登记 / 换钥流程关键词；
- `ansible-playbook --syntax-check`（update/install/check/service 四个 playbook，
  `inventory.example.ini`）→ 全部通过；
- `check:quick` → 7 gates 全绿（ruff / eslint / tsc / knip / compileall / gov-surface / ai-work）。

补充验证记录（2026-09-11，追加）：

- **隔离机制验证（零风险：本机 loopback sshd + 临时 known_hosts，不改任何系统文件）**：
  - ssh 层：未登记 → `No ED25519 host key is known ... Host key verification failed`
    （fail-closed）；已登记正确指纹 → 通过主机密钥校验（止步认证 `Permission denied`）；
    指纹不匹配（等效换钥）→ `WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!` + 硬失败；
  - ansible 层（本 PR 修复后配置 + 临时 inventory 指向 loopback）：未登记 →
    `UNREACHABLE` + `Host key verification failed`；已登记 → 通过主机密钥校验；
  - 全程未连接任何生产主机、未改系统 known_hosts，临时物已清理；
- **控制面覆盖率预检（只读，修正版）**：首轮以行文本解析 inventory、未展开父组
  `children`，把 2 个子组名误判为"未登记主机"；改用 `ansible-inventory`（权威）复核：
  `linux_hosts` 为父组（2 个 children），展开后 **14 台主机，指纹全部已登记，无缺口**；
- **P2 严格校验下只读连通验证**：以本 PR 修复后配置（`host_key_checking = True`）
  对 `linux_hosts` 全组执行 `ansible -m ping` → **14/14 SUCCESS**、
  0 UNREACHABLE / 0 FAILED——升级后现有运维链无 fail-closed 缺口；
- runbook §3 新增「控制机升级预检」小节（`ansible-inventory` 覆盖率盘点 + 单台只读
  验证 + 可选 fail-closed 对照；盘点用 600 权限临时文件且用后即删）。

仍未完成（pending）：

- `update_agent.yml` 真机升级演练（需隔离 / 预发布环境与维护窗口）；
- 未来 inventory 新增主机时按 runbook §3 先登记指纹再纳入（预检脚本已内置）。

## Revisit

- 若出现需要程序化 trust-on-first-use 的自动化场景，应作为显式 Provisioning 步骤
  设计（一次性带外确认），而不是重新打开 `StrictHostKeyChecking=no`；
- 运维规模扩大时可评估集中 known_hosts 分发（配置管理 / 签名清单），保留指纹
  人工确认环节。
