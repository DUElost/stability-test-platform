# Ansible 升级保护主机本地 MTBF 资源（#1248 / R14-F02）

Status: implemented
Class: bug-fix

## Decision

根因（本机 rsync 3.4.1 实证，非静态推断）：`update_agent.yml` 的代码同步用
`--delete --delete-excluded`，而 `resources/mtbf/` 不在 `agent_install_excludes`
里 → 主机侧布放的 MTBF APK 三件套被当 extraneous 删除；**即使补一条普通
`--exclude='resources/mtbf/'` 也不够**——`--delete-excluded` 的语义正是「连排除项
一起删」。API 热更新（`host_updater.py` 远端脚本）对同一路径用
`--exclude='resources/mtbf/'` + 普通 `--delete`（排除项天然免删）保护。

修复：角色默认值新增 `agent_host_local_paths`（语义 = 不传输 + 不删除），
playbook 的 dry-run 与真实同步对每项同时发 `--exclude='{{ item }}'` 与
`--filter='protect {{ item }}'`。两条 flag 由同一个循环成对生成，避免「只加
一半」的静默回归；dry-run 用同一规则，主机本地差异不会误触发同步/重启。

语义矩阵实证（rsync 3.4.1）：

| 形态 | 结果 |
|---|---|
| 只 `--exclude` | 本地文件被删（复现本 bug） |
| 只 `protect` | 源同名文件覆盖主机版本；主机独有文件仍被删（错误） |
| `--exclude` + `protect`（任意顺序） | 不传输 + 不删除，rc=0 无告警 = 热更新语义 |

## Alternatives

- **移除 `--delete-excluded`**：否决——它负责清理接收端历史残留（`tests/`、
  `install_agent.sh`、`__pycache__/` 等），为保护一个目录改变整体清理语义；
- **只加 `--exclude='resources/mtbf/'`**：否决——issue 已警告、实证确认在
  `--delete-excluded` 下仍被删；
- **只加 `--filter='protect resources/mtbf/'`**：否决——实证显示会传输源目录
  同名文件覆盖主机本地布放版本，与热更新「不传输」语义不一致；
- **host-local 项并入 `agent_install_excludes`、另维护 protect 列表**：否决——
  两表需成对修改，漏一处即静默回归；单表成对生成；
- **保护整个 `resources/`**：否决——超出本 issue 范围（其余带外资源的清理语义
  是既有设计），且与热更新只豁免 mtbf 不一致。

## Verification

实际运行：

- 本机 rsync 语义矩阵（脚本化探针）：exclude-only 删除 / protect-only 错误 /
  exclude+protect 正确（rc=0、stderr 空），含嵌套目录与主机独有文件；
- `pytest tests/test_rsync_host_local_protection.py tests/test_update_agent_playbook.py -q`
  → **11 passed**（新增 4 例：角色默认值声明 mtbf、playbook 两处调用均带成对规则、
  行为用例「主机本地文件存活 + 源码删除照常 + 排除清理不被破坏」、反例见证
  「只 exclude 会被 `--delete-excluded` 删」）；
- `pytest tests/ -q` → **106 passed**；
- `ansible-playbook --syntax-check playbooks/update_agent.yml`（example inventory）→ 通过；
- Jinja 渲染实证：两处 rsync 调用渲染后均含
  `--exclude='resources/mtbf/' --filter='protect resources/mtbf/'`，`bash -n` 通过；
- `python scripts/run_gates.py check:quick` → 7 gates 全绿。

未完成（pending）：

- 真实双机升级验证（控制面 rsync 到隔离 Agent 主机，确认 `resources/mtbf/`
  APK 存活）——本机为生产控制面宿主，不做跨机破坏性验证；隔离环境命令：
  跑 `update_agent.yml` 后
  `find /opt/stability-test-agent/agent/resources/mtbf -type f` 应仍有文件。

## Revisit

- 若后续把 `resources/` 整体改为「主机本地不清带外」，需重议
  `--delete-excluded` 与保护清单的边界（超出 #1248 范围）；
- 若 rsync 语义演进使「反例见证」用例变红（exclude-only 不再删除），说明保护
  规则可能冗余——重新核对后再决定简化，不要直接删用例；
- #1249（升级需先排空活跃任务/维护窗口）会改同一 playbook 的 pre/upgrade 段，
  与本单的 rsync 参数块不重叠，串行合入即可。
