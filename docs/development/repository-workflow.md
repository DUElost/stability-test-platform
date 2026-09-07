# 仓库开发与集成工作流

本文记录非代码交付物、并行 worktree 和 PR/CI 集成规则。命令与测试见
[`local-development.md`](./local-development.md) 和 [`testing.md`](./testing.md)。

## Agent Note

每个非平凡变更必须随 PR 附带或更新一条 Agent Note：

```text
docs/notes/{feature|bug-fix|simplification|architecture|process|testing}/yyyy-mm-dd-主题.md
```

记录 Decision、Alternatives、Verification、Revisit。纯机械改动豁免；方向级决策使用
ADR。模板与判定见 [`docs/notes/README.md`](../notes/README.md)。

## 并行 worktree

并行执行语义的权威源是
[`ADR-0034`](../adr/ADR-0034-multi-harness-execution-contract.md)（Accepted v1.0）与
[`execution-contract.md`](ai/execution-contract.md)；其 Registry（P1）尚未实现且
启动判据未触发，**现行操作规范为契约 §9 过渡条款**——下列规则（源自
[`2026-09-04-multi-agent-parallel-convention.md`](../notes/process/2026-09-04-multi-agent-parallel-convention.md)，
其并行语义已被 ADR-0034 取代、相关实践经过渡条款保留）继续有效：

- 冲突靠开工前查看实际 worktree diff 避免，不依赖手写 WIP 状态；
- 分片只用于冲突规避，不形成目录所有权；
- `AGENTS.md`、`CLAUDE.md` 及 Harness 共享规则同一时间只由一个 Execution 修改；
- 当前建议并发上限约 2–3，瓶颈以人的审阅吞吐为准。

派生视图：

```bash
for w in $(git worktree list --porcelain | awk '/^worktree /{print $2}'); do
  printf '%-46s -> ' "${w##*/}"
  git -C "$w" diff --name-only "$(git -C "$w" merge-base origin/main HEAD)" \
    | cut -d/ -f1 | sort -u | paste -sd, -
  printf '\n'
done
```

Execution Registry（`ai_work.py`）按 ADR-0034 P1 分期落地，启动判据与过渡安排见
`execution-contract.md` §9；判据未触发前不要手工维护任何登记状态。

## PR 与 Merge Queue

- `main` 启用分支保护，PR 是唯一合入路径；不要直推或手动点击 Merge；
- `.github/workflows/enable-auto-merge.yml` 维护 FIFO auto-merge，同仓库非 draft eligible
  PR 只有队首启用 auto-merge；
- `.github/workflows/pr-update-branch.yml` 与队列 reconcile 在队首通过 required checks
  且落后 `main` 时更新分支；
- fork、`frontend-major` 和 `github_actions` 更新不进入自动合入；
- required checks：`lint`、`CodeQL`、`pr-typecheck`、`pr-compileall`、
  `pr-agent-tests`、`pr-migrate-empty-db`；
- PR-Agent review 是异步顾问，不是 required check；security concern 通过独立 issue
  送达，不阻塞普通代理故障或超时。

Auto-merge 的队列与分支更新以 workflow 和
[`scripts/ci/pr-automerge-queue.sh`](../../scripts/ci/pr-automerge-queue.sh) 为事实源。

## 关单关键词写法

PR body 的 closing keyword（`Closes #N` / `Fixes #N`）由 GitHub 服务端在合入时
解析，与本机网络无关；**issue 号后必须跟空格或 ASCII 标点**——全角标点紧跟
（如 `#962。`）不构成引用、不会关单（#926/#929/#931/#963 四单同型实证）。
分支 commit message 内的关键词经 PR 合并不生效（merge commit 方式只扫
PR body），本仓禁止直推，故 PR body 是唯一关单通道——**合入后核销 issue
实际关闭**，未关按同因手工补。失效排查顺序：词边界 → 代码围栏 → base 分支。

## CI 分层

PR 合入路径只运行轻量 required checks。完整 backend tests、frontend tests/build 和
Docker build 由手工 full workflow 或 `main-ci-backstop.yml` 夜间兜底，避免把长任务
放进约两分钟的同步注意力窗口。

Dependabot 的 frontend patch/minor 可自动合入；frontend major、TypeScript major 和
GitHub Actions 生态更新需要人工评审。全量 CI 失败由 backstop 使用
`ci/backstop-failed` issue 去重通知，恢复后自动关闭。

相关取舍：

- [`2026-08-14-merge-path-attention-budget.md`](../notes/process/2026-08-14-merge-path-attention-budget.md)
- [`2026-08-29-serial-automerge-update-branch.md`](../notes/process/2026-08-29-serial-automerge-update-branch.md)
- [`2026-08-30-pr-agent-fully-async.md`](../notes/process/2026-08-30-pr-agent-fully-async.md)

## 文档维护

- 常驻文档写当前规则，不记录编年变迁；
- 变更历史进入 commit、PR、ADR 或 Agent Note；
- 代码和测试与文档不一致时，以代码和测试为准并回写权威文档；
- `docs/archive/` 只保存历史材料，不新增现行规范。

## 不变量违规处置（棘轮，#855 收口）

发现硬不变量/契约违规（review、CI、生产审计均可触发）时按序处置：

1. 查[强制力覆盖图](../design/2026-08-governance-surface-protection.md)：
   该不变量当前靠什么强制（运行时 / gate / 结构自证 / residual）；
2. 文本缺失（AGENTS.md 或锚点没有）→ 补文档；文本在场且**可机器查** →
   给差异面检查加策展模式；**不可机器查** → 在覆盖图 residual 列登记
   （review 兜底）；
3. 「没写到 vs 写了没传导」的归因只在选择修复端时需要：文本缺失=前者；
   在场但违规=后者，修复走第 2 步的机器查/residual，**不重建行为验证层**
   （2026-08-26 挂载裁决 + 2026-09-07 #855 收口）。

## Git 破坏性操作纪律（#930）

共享工作树 + 多会话并行环境下，破坏性 git 命令的爆炸半径是**他人进行中的
工作**，不只自己的：

| 操作 | 风险 | 处置 |
|---|---|---|
| `git reset --hard` | **不可逆**销毁未提交工作（含并行会话的） | **禁止**；未提交工作显式 commit/分支保存 |
| `git stash drop` / `clear` | **不可逆**销毁已暂存现场 | **禁止** |
| `git stash`（创建） | 可恢复，但污染全局 `refs/stash` 栈——多会话 pop/apply 错位会拿错别人的现场 | **禁止**，走显式分支 |
| `git stash list/show/pop/apply/branch` | 读侧/恢复侧 | 放行 |

**强制层**：

1. Claude 会话：PreToolUse hook（`.claude/settings.json` →
   `tools/dev/check_destructive_git.py`）对 Bash 命令按 shell 段解析，
   段首为 git 且命中上表禁止项即 exit 2 阻断；脚本 `--self-test` 红绿自证，
   自身异常 fail-open（不阻断）；
2. git 级观测：`.githooks/reference-transaction` 对 `refs/stash` 更新留痕
   告警（opt-in：`git config core.hooksPath .githooks`）——`reset --hard`
   的 worktree 破坏没有 git 级拦截点（ref 事务无法与普通提交区分），该命令
   依赖第 1 层 + 纪律；
3. 棘轮：同族命令（`git restore .` / `git clean -f` / `git checkout -- .`）
   暂未入拦截清单，出现事故按不变量违规处置流程扩展。
