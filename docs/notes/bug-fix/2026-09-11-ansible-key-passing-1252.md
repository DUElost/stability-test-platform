# 首次安装 inventory 完整传递 SSH 私钥凭据（#1252 / R14-F06）

Status: implemented
Class: bug-fix

## Decision

本质问题：`prepare_install_agent` 的凭据校验接受「密码或私钥」
（`agent_installer.py:59`），但生成的临时 inventory 无条件写
`ansible_password={creds.password}` / `ansible_become_password=...`，**不写
`ansible_ssh_private_key_file`**（原 `:73-79`）。仅配置非默认 `ssh_key_path`
且 SSH agent 未持有该密钥的主机：准备阶段通过、Ansible 连接必然失败。

修复 = inventory 生成按凭据形态组装：

- 有密码 → 写 `ansible_password` / `ansible_become_password`（与旧行为一致）；
- 有私钥 → 写 `ansible_ssh_private_key_file=<key_path>`；
- 空串凭据不再输出 `ansible_password=`（原写法在 key-only 下会输出空值键）。

## Alternatives

- **无条件同时写密码与私钥键**——放弃：空密码键会诱导 Ansible 走密码认证
  路径，且 inventory 语义含混；
- **把私钥内容复制进临时文件再传路径**——放弃：无必要（inventory 已受 600
  级临时文件语义保护，key 路径由运维侧管理）；复制私钥反而扩大凭据暴露面；
- **同时传 `known_hosts_path`（`ansible_ssh_common_args`）**——留待 Revisit：
  本 issue/验收聚焦私钥认证；主机密钥文件传递是独立主题（置后避免混入）。

## Verification

实际运行（worktree `/tmp/stp-1252`，基于 `origin/main`）：

- `pytest backend/tests/services/test_agent_installer.py -v` → **5 passed**
  （新增 2 例：key-only 写 `ansible_ssh_private_key_file` 且不含空密码键、
  密码路径回归不受影响）；
- **反向验证**：`git stash` 暂存修复 → key-only 用例失败（1 failed / 4 passed）；
  恢复后 5 passed；
- 测试清理加固：cleanup 放入 `try/finally`（断言失败也不残留
  `.stp-install-*.ini`；该模式已由 `.gitignore:74` 覆盖）——反向验证的失败
  路径复验无残留；
- `check:quick` → 7 gates 全绿。

未完成（pending）：

- 真机侧：仅私钥凭据主机经 RunConsole 完成一次安装（验收第 1 条的环境侧）——
  本机为生产控制面宿主，不在生产主机执行安装；需隔离环境以 key-only 主机验证。

## Revisit

- 若控制机依赖专用 known_hosts 文件（`STP_SSH_KNOWN_HOSTS` / `ssh_known_hosts_path`），
  安装链尚未传递——需要时以 `ansible_ssh_common_args=-o UserKnownHostsFile=...`
  形式补一次（与 #1263 严格校验配套）；
- 若出现「key-only + sudo 需密码」场景，become 仍需显式密码配置（属凭据模型
  本身，主机侧配置 NOPASSWD 或补 `ssh_password_enc`）。
