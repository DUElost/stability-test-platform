# 队首阻塞遥测（Queue Head Blocking Telemetry）

Status: implemented
Class: process

## Decision

新增只读工具 `tools/dev/queue_head_telemetry.py`，回答**一个问题**：

> 当前 FIFO 队首为什么没有前进，已经卡了多久？

它是 PR 自治方向上的 **P0（观测）**，不是状态机、不是冲突解决器。约束：

1. **integration 事实单一权威**：直接调用 `tools/dev/ai_work.py::derive_integration`，
   沿用契约 §3.1 的 `{NO_PR, PR_OPEN, READY, MERGED, CLOSED}` 五态。本工具
   **不引入任何新的 integration 顶层状态**，只把既有事实解释得更有用。
2. **队首身份读队列自己的产物**：FIFO 不变式「同仓库非 draft eligible PR 只有
   队首启用 auto-merge」→ 取 `autoMergeRequest` 非空的 PR。**不复制**
   `pr-automerge-queue.sh` 的资格谓词（draft / fork / dependabot 排除），避免
   两处编码「谁是队首」后漂移；多挂 auto-merge 时输出不变式告警。
3. **required checks 读分支保护**（`/branches/main/protection/required_status_checks`），
   不以硬编码列表为权威；API 不可用时回退并在 `required_source` 标注 `fallback`。
4. **`reason_code` 是 advisory telemetry**：`{NO_QUEUE_HEAD, NO_BLOCKER,
   REQUIRED_CHECK_PENDING, BEHIND_MAIN, REQUIRED_CHECK_FAILED, CONFLICTING}`，
   **任何 workflow / shell 不得据其分支**。出现 `if reason_code == ...; then`
   即意味着它已悄悄变成控制面契约，须另行裁决。
5. **零副作用**：不写持久化、不改 FIFO 行为、不新增 workflow / check run /
   label；GitHub 不可达时降级（沿用旧 integration + 标注 `observed_at`，不猜测）。
6. **`blocked_since` 是上界代理**（过渡，见 Revisit）：无持久化时取当前可观测
   信号——失败 required check 的 `completedAt`（取最早）、pending 的
   `startedAt`、冲突/落后的「main 首个领先提交」。冲突的真实起点不晚于此值，
   口径在输出中如实标注（`blocked_since_source`），不假装精确。

**命名刻意避开 `Integration State / Observed Integration State / PR Integration
Report`**：从命名层面阻止它被理解为第二套控制面。

首跑实证（2026-09-09 18:36 +08，真实队列）：

```text
queue_head: #1173 (docs/adr0035-merge)
integration: READY            # 权威五态
reason_code: CONFLICTING
owner: developer   actionable: true
mergeable: CONFLICTING  merge_state: DIRTY  behind_by: 77
blocking_checks: []           # required 全绿 → 冲突是唯一阻塞
blocked_duration: 21h39m      # 上界
```

即：队首 required checks 全绿、auto-merge 已挂，唯一阻塞是冲突，已卡约 22 小时，
10 个后续 PR 因此全部停在 `BEHIND`。这同时证伪了「先做 Conflict Classifier」的
优先级假设——**先有数据，再决定**。

## Alternatives

- **新建 `OBSERVED_INTEGRATION_STATE` 六态**：否决。与契约 §3.1 五态**同名不同义**
  （`READY` 两边含义不同），是同一层的第二套顶层枚举——正是「`ADR` → 再复制一份
  `semantic-surfaces.yml`」漂移模式的翻版；且 `Queue Position` 为 §3.1 明确排除项，
  加它是契约语义扩展（契约 §10：实现不得静默重新定义 Contract 语义）。
- **全量 PR 报告（14 个 PR 状态分布）**：否决。`BEHIND` 是 FIFO 串行 + `strict: true`
  的设计产物而非缺陷，分布近乎恒定，会诱导出「BEHIND 是最大问题 → 做自动 rebase」
  的错误结论。真正有信息量的是**队首阻塞时长 × reason_code**。
- **同时给 `scripts/ci/pr-automerge-queue.sh` 加遥测行**：延后，非否决。该脚本已
  fetch `head_json`，追加一行是零额外 API 的最优形态；但 #1173（当前 OPEN 且
  CONFLICTING）正在改同一文件，此刻改它必然制造本工具要观测的那种冲突。
  待 #1173 合入后作为独立小改落地（见 Revisit）。
- **在 Python 里复制队列资格谓词**：否决。两处编码「谁是队首」会漂移；读
  auto-merge 不变式使本工具在谓词变更时仍按队列**实际行为**读数。
- **引入持久化以记录精确 `blocked_since`**：否决（P0 不新增持久层）。先证明
  需要精确起点，再谈记录。
- **本阶段自动解冲突 / 自动唤醒 Agent**：明确不在范围内。P1（确定性分类）需先
  由本工具产出的数据证明其价值；P2/P3 涉及 ADR-0034 选择权原则（Registry 非调度器、
  不定义需求路由或自动下发）与仓库 write 权限模型，属执行语义变更，须先 ADR 裁决。

## Verification

- `venv/bin/python -m tools.dev.queue_head_telemetry --self-test`
  → `[OK] self-test：8 组红绿样例双向通过`（含实时样本「全绿 + CONFLICTING」、
  非 required 失败不计入阻塞、冲突优先级高于 BEHIND、时长格式化边界）；
- `venv/bin/ruff check tools/dev/queue_head_telemetry.py` → `All checks passed!`；
- 真实队列实跑（2026-09-09 18:36 +08）：输出见上，`required_source=branch_protection`、
  `queue_head_source=auto_merge_invariant`；
- 失败日志路径实测：`fetch_failure(34337998363)` →
  `F821 Undefined name `_monotonic`` + `backend/services/run_console.py:71:16`
  （即 #1162 当时的真实阻塞证据，用于验证 `REQUIRED_CHECK_FAILED` 分支）；
- **独立交叉验证**（本工具输出 vs reconcile 日志，两者互不依赖）：
  - 本工具：`queue_head=#1173  reason_code=CONFLICTING  behind_by=77  blocked_duration=21h39m`；
  - `Enable auto-merge` run 34341238984：
    `Queue head #1173 is 77 commit(s) behind main; updating branch.` →
    `X Cannot update PR branch due to conflicts`。
  - `behind_by=77` 与阻塞原因完全一致；且该报错串正是 #1173 自身 diff 要容错处理的那一条。
- `venv/bin/python scripts/run_gates.py check:quick`：见 PR 检查结果（pending→完成状态
  以 PR 上的 required checks 为准）。

## Revisit

- **#1173 合入后**：把遥测行接入 `scripts/ci/pr-automerge-queue.sh`（复用已 fetch 的
  `head_json`，零额外 API），并顺带打印进 `$GITHUB_STEP_SUMMARY`。这是本工具当前
  唯一的已知缺口（需要人工主动运行）。
- **数据判据（决定 P1 是否立项）**：连续观察队首 `reason_code` 分布。若
  `CONFLICTING` 长期接近 0，则确定性冲突分类器（P1）不立项；若占比显著且阻塞时长
  以小时计，才有实证依据进入 P1。**不得在拿到数据前先假设冲突是主要瓶颈。**
- **`blocked_since` 精确化的终态出口**：若「卡了多久」被证明需要真实起点（而非上界），
  改为在队首换档时记录一次时间戳（持久层新增须另行裁决）。
- **越界信号**：一旦有人写出 `if reason_code == ...` 这类分支，说明 telemetry 已
  成为事实上的控制面契约——立即停下并按契约面流程裁决，不要在脚本里悄悄扩权。
- **命名纪律**：若本工具被改名为 `*integration_state*` / `*integration_report*`，
  视为范围膨胀的前兆，需重新论证。
