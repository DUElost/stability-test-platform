# ai_work.py Execution Registry MVP（ADR-0034 P1）

Status: implemented
Class: feature

## Decision

按 [`execution-contract.md`](../../development/ai/execution-contract.md)（Living v1.1）§2–§5 实现 `tools/dev/ai_work.py`（P1 Registry MVP）。启动依据=契约 §9 v1.1 第一触发（已计划的多 Harness 批次启动前预置就绪，用户 2026-09-07 裁决）。

实现要点（与契约条款一一对应）：

- **§2.1 发现**：`git rev-parse --path-format=absolute --git-common-dir` → `<common-dir>/ai-work/`（registry.yaml + registry.lock 同目录，.git 内免跟踪）；
- **§2.2 九步原子写**：flock → read → validate（schema/枚举/scope 语法）→ modify → same-dir tmp → fsync → rename → 父目录 fsync → unlock；
- **§3 三维状态**：lifecycle 持久（CODING/FINISHED/ABANDONED）；liveness 由 `last_seen` 查询时派生（TTL 24h，advisory）；integration 由 `gh pr view` 派生刷新（READY=checks 全绿含 SKIPPED/NEUTRAL、MERGED/CLOSED=终态；**gh 不可用降级=保持旧值+observed_at**，不猜测）；
- **§3.2 真值表**：`in_risk()` 按「开放 PR 恒在窗口 / NO_PR 看 lifecycle / CLOSED 不单独出局 / MERGED 出局」实现——R23 的 `ABANDONED×PR_OPEN` 在窗有自测红绿样例；
- **§3.3 T1–T8**：`finish` 只写 lifecycle+登记 PR 号；`finish --abandon` 有开放 PR 时 stderr 警告并留窗（真值表保证）；`status` 严格只读（不取写锁、不刷 last_seen）；僵尸候选（STALE+空 effective scope）与 declaration-drift 双清单（声明未落地/diff 未声明）在 status 输出；
- **§5 effective scope**：`declared ∪ derived`，derived 三分档（worktree 在场=tracked staged/unstaged+**untracked**（`ls-files --others`）；worktree 删除=branch diff（用新增 `branch` 持久字段）；皆无=声明单独生效）；scope 拒绝规则（绝对路径/`..`/symlink 逃逸/trailing slash）+ 组件边界 overlap 谓词（`backend` ≠ `backend_new`）；
- **§6 test_impact**：declare 可缺省（=indirect）；
- **codec 自包含**：registry.yaml 用自写受限 YAML 子集（仅扁平 mapping+标量+字符串列表），超集语法 fail-fast——同时充当 validate 步骤，避免为已删工具的传递依赖（PyYAML）重新动 requirements/lock。

门禁接线：`run_gates` 新增 `ai-work` gate（self-test，入 `check:quick`）+ ci.yml lint job 新步骤「Execution Registry 自测」+ S5x `GATE_TO_CI_ANCHOR` 登记。

## Alternatives

- **用 PyYAML**——放弃：venv 可用但 requirements 未声明（属已删 run_gov_evals 的传递依赖），恢复声明须重生成 dev.lock；受限子集自写自读更符合 validate 语义。
- **status 也刷新 last_seen**——放弃：§2.5 观察不得改变被观察状态（R4）。
- **READY 判定读 required-checks 清单**——放弃：MVP 用 rollup 全绿近似（含 SKIPPED/NEUTRAL），不区分 FIFO 队首——与契约「不区分队首位置」一致。
- **把 derived 缓存进 registry**——放弃：derived 是 Git 事实现算（§5.1），缓存会引入第二份 diff 事实源。

## Verification

- `--self-test`：scope 归一化（含绝对路径/`..`/trailing slash 拒绝）、组件边界谓词（backend vs backend_new）、真值表全组合（含 ABANDONED×PR_OPEN 在窗、CODING×CLOSED 在窗、ABANDONED×NO_PR 出窗）、liveness 派生、codec 往返+未知字段/超集语法 fail-fast、九步原子写往返+validate 红样例——红绿双向全过；
- 端到端冒烟（真实 registry）：declare×2（缺省 test_impact）→ status（drift 双清单+overlap-hint 组件命中）→ `finish --abandon` 无 PR 出窗 → registry.yaml 落 `<common-dir>/ai-work/` 内容正确（branch 字段在档）→ 冒烟数据已清空；
- 治理门禁：`check_governance_surface.py --check`（S5x 校验新 gate↔CI 步骤配对）全绿；`ruff` 通过；
- pending：PR CI 六项 required checks 以实跑为准（ai-work self-test 新入 lint job）；`update --pr N` 的 GitHub reconcile 路径冒烟未做（需真实 PR 号，P1 批次首次使用时验证）。

## Revisit

- P2 Adapter：heartbeat 接入（`last_seen` 升格）、根 bootstrap 供给（#857）、Cursor 双份加载去重；
- P3 drift gate：status 的 drift/overlap 输出可直接作为 advisory 数据源；
- registry 无 GC：ABANDONED/MERGED 终态记录长期累积——量级极小（人工 declare），出现清理需求时按契约 §10 版本化增补。
