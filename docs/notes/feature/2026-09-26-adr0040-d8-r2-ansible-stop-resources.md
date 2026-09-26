# ADR-0040 D8 R2：Ansible 停止资源推送（2026-09-26）

Status: implemented
Class: feature

## Decision

按 ADR-0040 v1.2 D8 的 R1–R4 顺序（2026-09-26 owner 裁决）落第二步：Ansible 通道不再下发、也不再标记 `host-resources` 层。

- **`resources/` 整树归主机本地**：`agent_host_local_paths` 用 `resources/` 取代 `resources/mtbf/`，同步时 exclude + protect，
  既不下发、也不删除。rsync 3.4.1 真跑核对：源树没有 resources 时，主机的 aimonkey / flashtool / mtbf 整树存活；
  源树带内容不同的 resources 时，不传也不覆盖；只 exclude、不 protect 时整树被 `--delete-excluded` 清空（反例见证）。
  旧形态（只护 `mtbf/`）在源树没有 resources 时会清掉 aimonkey / flashtool，也就是 #2133 事故。
- **撤 #2166 源树断言**：改为 host-local 后，从任何源树（含没有 resources 的 git worktree）同步都安全，前置断言、
  `agent_allow_empty_resources` 放行开关与 summary 字段一起删除。原守卫测试文件由真跑的 rsync 用例接替；
  其中 `--syntax-check` 用例移到 `test_update_agent_playbook.py`，覆盖面扩到三份被改动的 playbook。
- **只算、只写 agent-code 身份**：`compute_deploy_digest.py` 只输出 `CODE_DIGEST`（与旧版在同一棵树上逐字节相同）；
  两份 playbook 都删掉 `ARTIFACT_DIGEST_RESOURCES` 的提取、写入和回读。主机上已有的该文件仍在保护清单内，由 R4 处置。
- **安装链不再拷 `resources/`**：`install_agent.sh` 的 `cp -r src/*` 改为 `copy_agent_tree`，跳过 `resources/`，
  目标侧已有的 resources 不动。
- **补一个 D8 漏算的消费方**：安装脚本 §4b 原来从 `resources/flashtool` 拷 SP Flash Tool 自带的 `99-ttyacms.rules`
  （让 ModemManager 忽略 MTK 设备，刷机期间防止它抢占 preloader/BROM 串口）。停推之后新装主机会缺这条规则，
  所以改为写固定内容，与 `flashtool@1.2444.00.100` 工具包根目录的原件逐字核对一致。供给面有三处：安装脚本、
  `ensure_flash_prereqs.yml`（强制执行）、`update_agent.yml` 的 opt-in 段（受 `agent_ensure_flash_prereqs` 门控），
  由测试锁定三处同源。ADR 的 D8 R2 条目已就地加注勘误。

## Alternatives

- **install 的 copy 改用 `ansible.posix.synchronize` 并排除 resources**：可以省掉一次传输，但要改动装机的认证路径
  （password / sshpass），缺少真机安装验证；弃。现状下，源树若带 resources，会被传到主机暂存目录再由安装脚本丢弃，
  属过渡性浪费。R3 之后 bundle 不再携带 resources，这次传输自然消失。
- **MM 规则走 wrapper 子命令**：会扩大提权面，需要联审 ADR-0037；安装（root）与 Ansible（become）两条路已足够；弃。
- **保留 `resources/mtbf/` 条目并追加 `resources/`**：前者被后者覆盖，留着只会成为死配置；弃。

## Verification

- 相关 11 个测试文件 **128 passed**，其中 rsync 真跑 3 条新用例：源树无 resources、源树资源不下发、只 exclude 不 protect 的反例；
  另有 `copy_agent_tree` 沙箱真跑、MM 规则三面同源，以及三份 playbook 的 `ansible-playbook --syntax-check`
  （改坏 playbook 后返回 rc=4，确认该用例有判别力）
- **旧源码反证**：把 7 个源文件换回 origin/main 版本，新用例 **14 条全红**，包括两条 rsync 真跑用例
- `compute_deploy_digest.py` 新旧版在同一棵树上 `CODE_DIGEST` 逐字节一致（`sha256:6041510c…`）
- `check:quick` **16 gates OK**；`tests/` 全量 **1914 passed / 18 skipped**（rebase 到含 R1 的 main 后相关用例复跑绿）

## Revisit

- 本 PR 没有部署动作：Ansible 改动在下一次有人跑 playbook 时生效。存量主机的 `99-ttyacms.rules` 本来就在，
  需要时可由 `ensure_flash_prereqs.yml` 收敛。
- R3（release manifest 去 `host-resources` 分量）→ R4（Agent 心跳 / wrapper 子命令 + ADR-0037 回填 / DB 列 / 前端；
  同时处置主机上的 `ARTIFACT_DIGEST_RESOURCES`）。跟踪 #3288，台账 `host-resources-layer`。
