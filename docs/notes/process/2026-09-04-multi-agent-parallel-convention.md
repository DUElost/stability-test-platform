# 多 Agent 并行开发约定

Status: implemented
Class: process

## Decision

`AGENTS.md` 新增「多 Agent 并行开发」一节，三条约定：

1. **冲突靠避免，不靠解决**：开工前跑派生视图（对 merge-base 取差异）看各 worktree
   正在动哪些顶层目录。取**派生**而非**声明**——手写状态会过期，而过期状态比没有更危险。
2. **分片只是冲突规避手段，不是职责边界**：跨片任务照做，不限制单个 agent 能改什么；
   只守「同一时刻别让两个 agent 改同一批文件」。此条明写是为了保住 agent 自由度。
3. **唯一硬规则 —— 元文件串行化**：并行 agent 不要顺手改 `AGENTS.md` / `CLAUDE.md`。
   需要改就记进 PR 描述或 issue，由人或专门的 docs agent 在独立 PR 里统一改
   （auto-merge 会串行化）。

并给出**并发上限 ≈ 2–3**。瓶颈是人的审阅吞吐（N 个 agent = N 份 PR 要审），不是
agent 互相阻塞——CI ~2 分钟出结果 + FIFO auto-merge 已把 agent 侧阻塞降到零。

涉及文件：`AGENTS.md`（新增一节）。纯文档变更，无代码 / 测试 / env 影响。

## Alternatives

- **手写 `docs/notes/process/WIP.md` 意图公告**（曾作为建议提出）——否决：
  ① 它防的失败已被第 2 条防住，属重复设防；② 手写状态会过期，而你会信它；
  ③ 读 + 写 + 解冲突本身就是「agent 精力花在协同上」。替代为派生视图：零维护且必然准确。
- **subagent / team 机制互发消息**（`TeamCreate` / `SendMessage` / 共享 TaskList）——否决：
  并行 subagent 上下文隔离，易产生不一致决定；且新增一份需人审阅的交互产物，
  与合入路径注意力预算相悖。
- **把分片写成职责边界**（如「agent 只碰 `frontend/`」）——否决：跨片任务无法做，
  且过度约束会逼 agent 为守边界而拆碎 PR。改为只约束「同一批文件的并发」。
- **为 N≥5 设计协同协议**——否决：N 增大后瓶颈完全在审阅端，加协同机制不提升吞吐；
  正确做法是任务排队。

## Verification

- 派生视图命令已在本仓库实跑验证，能同时反映已提交与未提交改动。初版用
  `origin/main...HEAD` 只看提交态，漏掉未提交改动（当场发现并验证），
  已修正为 `git diff --name-only "$(git merge-base origin/main HEAD)"`。
- 实证：2026-09-04 曾出现两个 worktree 同时改 `AGENTS.md` / `CLAUDE.md`
  （`docs/drift-sync-2026-09-04` 与 `docs/adr-0033-h1-rulings`），即本 note 第 3 条
  要防的形态；二者由 FIFO auto-merge 串行合入（#845 02:31 → #846 02:39，相隔 8 分钟），
  期间 rebase 由 `pr-update-branch` 自动完成。
- 纯文档变更：`lint` / `pr-typecheck` / `pr-compileall` 不受影响。

## Revisit

- 若并行 worktree 长期 ≥4 个、派生视图成为每日必跑，可把命令收进 `scripts/` 或做成
  `scripts/run_gates.py` 的 checker（判据：每周跑三次以上）。
- 若 `AGENTS.md` / `CLAUDE.md` 改动频率升到每周多次，元文件串行化成本将不再近乎为零，
  需重议（可能改为按节划分所有权）。
- 若出现 agent 因跨片任务被边界规则卡住的实际案例，说明第 2 条措辞不够，需补反例。
