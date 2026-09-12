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
[`ADR-0034`](../adr/ADR-0034-multi-harness-execution-contract.md)（Accepted）与
[`execution-contract.md`](ai/execution-contract.md)。Execution Registry（P1，
`tools/dev/ai_work.py`）已落地并被采用——契约 §9 启动判据第 1 条（已计划的
多 Harness 批次启动前预置就绪）已触发，**现行操作规范为契约正文协议**：
开工 `ai_work.py status` 前检 + `declare` 领单，收尾 `finish` 与 GitHub
reconcile；派生视图不再是主操作规范，降为 ground truth 交叉验证手段（契约 §9）。
下列规则（源自
[`2026-09-04-multi-agent-parallel-convention.md`](../notes/process/2026-09-04-multi-agent-parallel-convention.md)，
其并行语义已被 ADR-0034 取代）继续有效：

- 冲突靠开工前 Registry 前检与实际 diff 交叉验证避免，不依赖手写 WIP 状态；
- 分片只用于冲突规避，不形成目录所有权；
- `AGENTS.md`、`CLAUDE.md` 及 Harness 共享规则同一时间只由一个 Execution 修改；
- 并发不设会话数上限（ADR-0034 §2.6 v1.8）；瓶颈在集成收尾侧（审阅吞吐 + 平台可靠性），在窗 Execution 规模与 reconcile 负载为实测代理，恶化时重议。

派生视图（交叉验证；`effective_scope = declared ∪ derived` 中 derived 是 Git
事实、声明不能覆盖，契约 §5.1）：

```bash
for w in $(git worktree list --porcelain | awk '/^worktree /{print $2}'); do
  printf '%-46s -> ' "${w##*/}"
  git -C "$w" diff --name-only "$(git -C "$w" merge-base origin/main HEAD)" \
    | cut -d/ -f1 | sort -u | paste -sd, -
  printf '\n'
done
```

Registry 细则（写入协议、scope 语法与 overlap 谓词、三维状态、drift、reconcile）
见 `execution-contract.md` §2–§7。

## Registry 批次收窗与 drift 留痕（#1097）

`ai_work.py drift`（run_gates `ai-drift` gate，advisory 不阻塞）只在 registry
宿主机产生信号（`check:full` 或按需运行）；CI runner 无 registry 数据，接
PR/CI 恒 no-op，故**不接入**，留痕靠下述收窗纪律（论证见 issue #1097）：

- **先批量核销再收窗（#1234）**：`python tools/dev/ai_work.py update --all`
  ——一次 gh 调用刷新全部已登记 PR 的终态（MERGED/CLOSED），一次九步写落盘，
  **不刷任何 `last_seen`**（无 execution identity 即不冒充心跳）。逐条 `update`
  刷上百条记录是分钟级；批量是秒级，且能一次清掉「已合入但缓存仍称开放」的
  陈旧记录（实测 22 条一次清空）；
- 批次收窗与合入核销/reconcile 前先跑 `python tools/dev/ai_work.py drift
  --strict`；有提示先处置再收尾——STALE 记录人工裁决、declaration-drift
  补/收窄 scope、overlap 改串行；
- 开工前检看风险面用 `python tools/dev/ai_work.py status --risk`（只列在窗
  记录 + overlap 提示；全量仍用不带 `--risk` 的 `status`）；
- 输出与处置结论随批次收尾评论留痕（#1035 Evidence 台账回溯组同载体）。

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

### 队首停摆的判读与处置（#1246）

队首 required check 未通过时，队列**不再只是日志一行**：reconcile 会开/更新一个
`ci/queue-blocked` 去重 issue（同型 `ci/backstop-failed`；指纹未变不刷屏，队首恢复
或队列清空自动关闭）。告警只承载可见性，不参与合入决策；issue 操作失败不影响队列
行为。

收到告警（或怀疑停摆）时：

1. **判读**：`python -m tools.dev.queue_head_telemetry`——只读输出队首、阻塞原因
   （required check 未过 / 落后 main / 冲突）、已卡时长与失败日志；
2. **处置（人工，择一）**：修复该 check（owner）/ 解冲突 / 让位（把该 PR 关闭或改
   draft）；随后 reconcile（每小时 cron 或任意 PR 事件）自动推进并关闭告警；
3. `main` 合入纪律不因停摆豁免——**不要手动 Merge**。

> 红队首的「自动重基逃生」未启用（宽松谓词会每小时重刷红 head 的全量 CI）；
> 如再现「陈旧红」（check 结果早于 main 推进、重基后可绿）实证，按收紧谓词另行立项。

## 关单关键词与自动关单

closing keyword（`Closes #N` / `Fixes #N`）由 GitHub 服务端在**合入**时解析并
关闭关联 issue——但仅当**合入执行者是人类身份**。

**根因（2026-09-08 对账 + 沙盒实验钉死，#1101）**：mergedBy=github-actions
（GITHUB_TOKEN）的合入不触发 linked-issue 原生关闭，且其产生的
`pull_request: closed` 事件被级联抑制（不触发新 workflow run）——与
「GITHUB_TOKEN 事件不产生新 run」同族。mergedBy=DUElost（人类 token）的合入
4/4 秒级原生关闭。关键词写法（全角括号、句号、空格）均非失效因子；2026-09-07
「平台故障窗口」假说已证伪（当日 TLS/GraphQL 异常与关单失效仅为时间重合）。

**机制**：auto-merge 由谁最后启用，GitHub 就以谁的身份执行合入。队列
reconcile（enable-auto-merge.yml / pr-update-branch.yml）以 `AUTO_MERGE_PAT`
（人类身份 fine-grained PAT，仅限本仓库 Contents/Pull requests RW；未配置时
回退 GITHUB_TOKEN）启用队首 auto-merge，因此队列合入落在 DUElost 身份上，
原生关单与事件级联均正常。

**纪律**（不因机制修复而免除）：

- 合入后**核销 issue 实际关闭**（查 issue state，勿以 PR body 为准），未关即
  手工补关（附证据评论）；
- `main-ci-backstop.yml` 每日 PASS 后以 `closingIssuesReferences` 兜底补关；
  其「main 前进即整体跳过」guard 意味着密集合入日可能整天不补——兜底不可
  依赖，手工核销仍是第一道；
- 排查顺序：合入执行者身份（`mergedBy`）→ `closingIssuesReferences` 是否
  建立 → 关键词写法。

## CI 分层

PR 合入路径只运行轻量 required checks。完整 backend tests、frontend tests/build 和
Docker build 由手工 full workflow 或 `main-ci-backstop.yml` 夜间兜底，避免把长任务
放进约两分钟的同步注意力窗口。

### 夜间红灯的前移触发规则（#1525 决策）

夜间 `main-ci-backstop` 红灯 = 缺陷已合入 main、最迟次日暴露（敞口 ≤24h）。敞口不
引入 Merge Queue 处理（成本 ≈10× 全量 CI/天；数据不支持），按**类**前移：

- **同类夜间红灯 ≥2 次 → 评估将该类前移为 PR 侧检查**（required 或信息性）；单次
  偶发不动作；
- 前移先例：迁移空库类 → `pr-migrate-empty-db`（required）；agent 类 →
  `pr-agent-tests`；repo-level 根测试类 → #1569（在途）。

当前分层：

- **PR 侧 required**：`lint`、`CodeQL`、`pr-typecheck`、`pr-compileall`、
  `pr-agent-tests`、`pr-migrate-empty-db`；
- **PR 排除**（main push / 手工 dispatch / 夜间兜底跑）：`backend-test`、
  `frontend-check`、`docker-build`；
- 决策量化依据（近 30 次 backstop：9 红夜 ≈ 每 3 天/每 33 PR 一次；`backend-test`
  类占 5/6、前端/docker 零次）：见
  [`2026-09-12-pr-gate-promotion-rule-1525.md`](../notes/process/2026-09-12-pr-gate-promotion-rule-1525.md)。

Dependabot 的 frontend patch/minor 可自动合入；frontend major、TypeScript major 和
GitHub Actions 生态更新需要人工评审。全量 CI 失败由 backstop 使用
`ci/backstop-failed` issue 去重通知，恢复后自动关闭。

相关取舍：

- [`2026-08-14-merge-path-attention-budget.md`](../notes/process/2026-08-14-merge-path-attention-budget.md)
- [`2026-08-29-serial-automerge-update-branch.md`](../notes/process/2026-08-29-serial-automerge-update-branch.md)
- [`2026-08-30-pr-agent-fully-async.md`](../notes/process/2026-08-30-pr-agent-fully-async.md)
- [`2026-09-12-pr-gate-promotion-rule-1525.md`](../notes/process/2026-09-12-pr-gate-promotion-rule-1525.md)

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
