# 队首 update-branch 撞 workflow scope 不再整 job 红（#1783）

Status: implemented
Class: bug-fix

## Decision

`scripts/ci/pr-automerge-queue.sh` 的 `update_branch_tolerant()` 已对三类失败做无害化
（head-sha 竞态 / 真冲突 / PR 已非 open），但**PAT 缺 `workflow` scope 的拒绝**落到
`return "$rc"`：队首 PR 只要改过 `.github/workflows/*`，`gh pr update-branch` 就被
GitHub 拒绝（`refusing to allow a Personal Access Token to create or update workflow
... without 'workflow' scope`），**整个 reconcile-queue job 变红且队首 rebase 停摆**
（实测 run 34689776040 后 28 个 open PR 全部 `behind`，直到人工合入队首才恢复）。

按 issue 的路径 **A** 补齐第四个容错分支（最小改动、零安全影响）：

```bash
if printf '%s' "$out" | grep -qiE "without .?workflow.? scope"; then
  echo "PR #${num} touches .github/workflows/* and the queue token lacks 'workflow' scope;"
  echo "  ... Rebase it manually with a workflow-scoped credential ..."
  return 0
fi
```

判据、文案与既有三类同构：这是**可处置状态**（凭据配置），不是本 job 故障；
绿退 + 人工动作指引，避免把可处置状态每轮刷成红叉（`reconcile-queue` 不在 required
checks 内，不阻塞合入，但红叉本身会像 #1761 的告警饱和一样失去信息量）。

**路径 B（根因）不做**：给 `AUTO_MERGE_PAT` 补 `workflow` scope 等于允许该凭据改写
CI 定义，属安全面扩张，按 issue 要求由 owner 单列决策（见 Revisit）。

## Alternatives

- **只做路径 B（补 PAT scope）**：弃（本单）——见 Decision，安全面扩张需 owner 决策；
  且即使补了 scope，其它凭据形态（token 轮换、fine-grained PAT 权限回收）仍可能
  再次拒绝，容错分支仍有价值；
- **改用 `GITHUB_TOKEN` 并在 workflow 里声明 `permissions: workflows: write`**：
  备选根因方案——`pull_request_target` 上下文的 GITHUB_TOKEN 在授予 `workflows`
  写权限时可改 workflow 文件；但改的是队列的鉴权语义（PAT ↔ GITHUB_TOKEN 的权限
  差异、审批流 `approve_pending_workflow_runs` 的既有假设），需独立裁决与实测，
  不在本单顺手切换；
- **事前判定而非错误文案匹配**：`gh pr view --json files` 先看是否含
  `.github/workflows/*`，含则直接跳过 update-branch——更稳（不依赖错误文案），
  但每个队首多一次 API 调用、且与既有三类「事后识别」风格不一致，列入 Revisit；
- **队首含 workflow 变更时直接报错退出**：弃——正是本单要消除的「整 job 红 + 队列
  停摆」，把可处置状态升级成队列故障。

## Verification

- **红绿对照**（`tests/test_automerge_queue_alerts.py`）：
  - 新增 `test_workflow_scope_rejection_keeps_reconcile_green`：绿队首 + behind=2 +
    注入 GraphQL workflow-scope 拒绝 → 断言 returncode 0、update-branch 确实被调用、
    stdout 带 `workflow`/`scope` 关键词与 rebase 指引；
  - **反例**：还原旧脚本 → `1 failed`（returncode 1，line 306）；本 PR → **13 passed**；
- 既有 12 例（告警/指纹/红头/空队列等）全绿，未回归；
- `python scripts/run_gates.py check:pr` → 见 PR 校验记录（含 repo-tests 离线子集）。

未做：真实队列端到端复现（需造一个改过 `.github/workflows/*` 且落后 main 的队首 PR
并等待 reconcile run）——判据与三段既有容错分支同构，用注入式单测覆盖。

## Revisit

- **路径 B 待 owner 裁决**：`AUTO_MERGE_PAT` 是否补 `workflow` scope（或改用
  GITHUB_TOKEN + `permissions: workflows: write`）。补 scope 后本容错分支成为兜底，
  不冲突；
- **文案匹配的脆性**：本分支（与既有三类一致）依赖 GitHub 错误文案。若上游改文案，
  分支失效 → 又回到整 job 红。硬化方案=事前 `gh pr view --json files` 判定（见
  Alternatives），但要为每次队首判定增加一次 API 调用；
- **人工 rebase 的落点**：当前只打印指引，没有形成可追踪的待办（人工看日志）。
  若此类 PR 变多，可在指引里附一次性命令或让告警 issue 带上「需手动 rebase」标签，
  属告警通道增强，可在 #1761 批次里一并考虑。
