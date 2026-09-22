# PR 终态判读口径入文档：REST `.state` 对已合入返回 `closed`（#3160）

Status: implemented
Class: process

## Decision

把「判读 PR 终态」的**查询口径**写进两处权威面：

1. `docs/development/repository-workflow.md` §PR 与 Merge Queue —— 判终态用
   `gh pr view <N> --json state`（`OPEN`/`MERGED`/`CLOSED`）；REST `GET /pulls/<N>` 的
   `.state` 只有 `open`/`closed`，**已合入也返回 `closed`**，合入事实在独立字段 `.merged`；
2. `docs/development/closure-cheatsheet.md` §A —— 僵尸 ② 判据旁加同一口径（收口场景是误判发生地）。

只钉口径，不改任何工具行为、不改 registry 风险真值表，也不动 `finish --abandon` 纪律。

### 为什么值得进文档

误判代价不是多发一条通知，而是**走错流程**：把「已合入」当成「关闭未合（僵尸 ②）」，下一步就是等价核验 → owner 授权 `finish --abandon` → 给已合入的工作打备份 ref 并记错终态。已有收口判据（`closure-cheatsheet.md` A 节、`repository-workflow.md` worktree/分支章节）都默认「PR CLOSED 未合」是可信读数，没有任何地方写明这个陷阱；挂长轮询等队列合入（本次做法）会稳定踩到。

## Alternatives

1. **只在监控脚本里规避（把判据写成 `state==closed and merged==false`）** —— 拒绝。脚本不在本仓（本次是一次性命令），规避只覆盖写它的那一次；口径必须落在读者会查的文档里。
2. **改 `gh` / REST 语义** —— 不可行，非本仓可控项。
3. **只在 `closure-cheatsheet.md` 加一行** —— 不够。该页自述「一页速查，不新增规范」，权威源在 `repository-workflow.md`；两处都写才能既覆盖「在哪查」也覆盖「误判发生后在哪看到」。

## Verification

- **实证口径**（对已合入的 #3141 / merge commit `268b5eb4`）：
  - `gh pr view 3141 --json state -q .state` → `MERGED`；
  - `gh api repos/DUElost/stability-test-platform/pulls/3141 -q '.state, .merged'` → `closed merged=true`。
- `python scripts/run_gates.py check:quick`：全绿（含 gov-surface 对 `repository-workflow.md` 的断链防护）。
- 两处新增文本未引入新链接，断链面不变。

## Revisit

若将来把「等队列合入」固化成工具（轮询/监听），其判据直接引本文档口径；不为此新建监控文档。
