# update_agent.yml 前置断言：源树缺 resources 即拒绝（#2166）

Status: implemented
Class: bug-fix

## Decision

`update_agent.yml` 在 `pre_tasks` 增加「源树必须携带可分发 resources」的
**fail-closed 前置断言**（在任何落盘/快照动作之前）：

- `ansible.builtin.stat`（**`follow: true`**）校验 `<agent_source_dir>/resources`
  是目录；
- `ansible.builtin.find`（`patterns=flash_tool, recurse=true, file_type=file,
  follow=true`）校验至少一个 flashtool 入口；
- 两者任一不满足即 `assert` 失败并给出可执行指引（用主 checkout / 拷入或软链
  resources / 显式 `-e agent_allow_empty_resources=true`）；
- 放行开关 `agent_allow_empty_resources`（`group_vars` 默认 **false**）打开时：
  前置阶段打 `WARNING`，`Print update summary` 增列 `allow_empty_resources`。

**背景（#2133 实测事故）**：playbook 以 rsync `--delete` 同步 agent 树，而
`backend/agent/resources/`（230MB）**不在 git**——任何 worktree 都没有它；
源树缺 resources 时主机侧被当成"已删除"清空（.82 从 230M 削到 9.3M、
flashtool 全失，canary 失败）。恢复走 `POST /hosts/{id}/hot-update?force=true`
（payload 取控制面主树）10.9s 复原。

**两个实现坑（实测）**：

1. `fileglob` lookup **不跟随目录软链**——用「软链 resources 入 worktree」的合规
   做法会被误判缺失（首版实现如此，功能验证时命中）；
2. ansible-core **2.19 起 `stat.follow` 默认改为 `no`**——软链目录 `isdir=False`
   使断言恒假（同一验证轮命中）。故 stat 显式 `follow: true`，且这两点在测试里
   锁定（防回归）。

## Alternatives

- **只写文档"别从 worktree 跑 playbook"**：否决——执行契约要求 harness 在
  worktree 干活，靠口头纪律必然重演（本次即代价）。
- **对 resources 采用 `mtbf/` 式 host-local 保护（源缺则不清）**：否决——会
  静默跳过资源分发，主机资源面长期漂移且掩盖误用；fail-closed+显式放行更可观测。
- **默认放行 + 仅警告**：否决——#2133 的损害正是"静默清空"，必须默认拒绝。
- **只查 `resources/*` 非空**：否决——具体损害点（flashtool）要单独可判，
  报错信息才能一眼定位。

## Verification

- 结构/回归用例：`tests/test_ansible_resources_guard_2166.py` **6 passed**
  （断言在 `pre_tasks` 且早于首个落盘任务、受开关门控、`find follow=true`、
  `stat follow=true`、summary 标注、`group_vars` 默认 false、`--syntax-check`）；
- **功能双路径实测**：无 resources 的 worktree 实跑 → 断言拒绝、
  `changed=0`（**未触碰主机**），报文含三条指引；把真实 resources 软链入树后
  `--check` → 断言 `ok`（follow 语义生效）；
- 相关存量测试 75 passed（digest 契约/簿记、host-key、secret 来源、provisioning
  前置、wrapper 边界、安装脚本两份）。

## Revisit

- #2134 的 B 步（34 台迁 wrapper）用同一 playbook——本守卫先行落地，B 步执行时
  从**主 checkout**（或带 resources 的树）跑；
- 若 resources 未来改随包分发（ADR-0033 Tool Contract / 包存储）或按需下发，
  本断言可退役（届时删除守卫与开关，并更新本 note）；
- 恢复入口（`force` 热更新复原 resources）建议同步进 runbook（当前只在本
  note 与 #2133 评论里）。
