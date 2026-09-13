# 合并队列纪律：禁止 Execution 自持 auto-merge / nudge（#1746 队列抖动复盘）

Status: implemented
Class: process

## Decision

复盘 #1746 合入后出现的两类队列异常（`queue_head_telemetry` 的「多个 PR 挂了
auto-merge」告警 + 个别 PR auto-merge 反复 enable/disable），确认来源是**并行
harness 绕过队列自行操作 auto-merge**，据此落两条约束：

1. **规范面**：`AGENTS.md` 总原则与 `docs/development/repository-workflow.md`
   的「PR 与 Merge Queue」补明文——Execution 只把 PR 跑到「就绪 + Registry
   登记」，不得自行 `gh pr merge --auto` / GraphQL `enablePullRequestAutoMerge`
   （含 `--squash`），也不得替他人的 PR 做 update-branch / nudge；
2. **平台面**：仓库设置关闭 squash 合并（`allow_squash_merge=false`），使
   `--squash` 直接失败、自动回落到队列的 `--merge`。

### 排查证据（2026-09-13）

- **执行者**：Cursor 长会话
  `~/.cursor/projects/home-debian13-stability-test-platform/agent-transcripts/b8a4bafc-0880-4949-9349-cbb8ae2012b5`
  （`cursor-agent --yolo` 的子终端，多 worktree：`/tmp/stp-1698`、`/tmp/stp-1711`、
  `/tmp/stp-741`…）。终端原文（`terminals/77645–77667.txt`）显示其收尾固定三段：
  ① 自己 PR `gh pr merge <n> --auto --squash`；② 「nudge」更早的 PR
  （`gh pr update-branch <n>` + `gh pr merge <n> --auto --squash`，一次覆盖
  1697/1700/1712/1713/1718/1720–1723 等）；③ 约 40s 轮询，发现自己的 auto-merge
  被队列关掉就重挂。部分路径直接用 GraphQL `enablePullRequestAutoMerge`。
- **后果实证**：近期合入中 **#1724、#1741 是唯二的非 merge 提交**（squash），
  其余均为队列的 `Merge pull request #N` 形态；主干累计 48 个 squash 形态提交，
  集中在 2026-08-03/04 与 09-11/12/13（与 Cursor 会话活跃期吻合）。
- **受影响通道**：Registry 的 landed 判定依赖 merge commit 主题
  （`tools/dev/ai_work.py:470-473`，`git log --merges` + `^Merge pull request #N from`），
  squash 会让该通道静默失效（`docs/notes/process/2026-09-10-registry-risk-cache-invalidation.md`
  的 Revisit 已预告该脆弱点）。
- **排除项**：`.codex` 的 thread history 有历史手动 enable（PR 252–330，用
  `--merge`，17xx 段零活动）；muti-cursor 的定时扫描是 Codex `-s read-only`；
  claude/codebuddy 各会话只有文本讨论，无执行。
- **判读提醒**：auto-merge 事件的 actor 一律是同一个 PAT（队列 workflow 亦用它），
  **不能据 actor 判定人工操作**。

## Alternatives

- **只依赖队列 reconcile**：现状即可纠正，但代价是 enable/disable 抖动、CI 空转，
  且 squash 合入会在两次 reconcile 之间漏过（已实际发生 2 次）——否决；
- **只改 Cursor 本地规则（不入库）**：不入库则其他 harness 无约束、换机丢失——
  否决；
- **同时关 `allow_rebase_merge`**：超出本次范围，且无实证 rebase 被滥用——不做；
- **新增 CI 检测「非队首持有 auto-merge」**：队列 reconcile 已覆盖（事件 + 每小时
  cron），新增检测是重复机制——不做。

## Verification

- `gh api repos/DUElost/stability-test-platform`：`allow_squash_merge: true → false`
  （PATCH 后复查，`allow_merge_commit`/`allow_rebase_merge` 保持 true）；
- `python tools/dev/check_governance_surface.py --check`：S1–S13、S5x 全绿；
- `python scripts/run_gates.py check:quick`：7 gates 全绿；
- 队列侧旁证：约束落地前 #1774（该会话正在推进的 PR）最终以 **merge commit**
  形态合入（`e9cb3caf Merge pull request #1774`），未被 squash。

## Revisit

- 观察该 Cursor 会话在 `allow_squash_merge=false` 后的行为：若其改为
  `--rebase` 或继续 nudge，则需要更高一层的约束（ruleset 或会话提示）；
- 若「多持有者」仍反复出现，考虑在队列 reconcile 里把该告警从遥测 warning
  升级为 issue（当前只在 `queue_head_telemetry` 打印）；
- #1764（ci/queue-blocked 误报修复）合入后，复查告警通道噪音是否消除。
