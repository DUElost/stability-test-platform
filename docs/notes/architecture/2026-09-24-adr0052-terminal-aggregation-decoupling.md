# Agent Note：ADR-0052 草案（终态事实与父 Run 聚合解耦）+ 容量 P0 上线手册

Status: proposed
Class: architecture

- 日期：2026-09-24
- 范围：**文档**——`docs/adr/ADR-0052-*.md`（Proposed v0.1）、`docs/operations/2026-09-24-capacity-p0-rollout-runbook.md`、三处索引（`docs/adr/README.md`、`docs/DOC-MAP.md`、`docs/operations/README.md`）
- 关联：[#3244](https://github.com/DUElost/stability-test-platform/issues/3244)（P1 实施单，本稿是其第一步）、[#2959](https://github.com/DUElost/stability-test-platform/issues/2959)（父单）、[ADR-0026](../../adr/ADR-0026-plan-execution-scaling.md)（被替代 §6 的宿主）、[ADR-0047](../../adr/ADR-0047-db-pool-and-connection-capacity.md)（P0 已裁决）
- 未做（明确）：不实施、不转 Accepted、不动生产数据库/配置。**本 PR 只有文档。**

## Decision

1. **新建 ADR-0052，而不是改写 ADR-0026 正文**；在 §3 用表格逐条标明**替代其 §6 的两处**（终态事务内自增计数 / 每 Job 持父行锁）、**继承其两点**（单一 terminalization 入口 / 集中服务 + 对账 sweep 自愈），并明确**不动 ADR-0048 D1**。
2. 决策边界按 owner 2026-09-24 口径成文：D1 父级聚合行彻底移出 Job 终态事务（含删逐 Job `acknowledged_job_ids` 写）；D2 触发必须持久化（insert-only pending 标记 + 提交后唤醒，Redis 仅传输，`counter_reconciler` 只作修复路径）；D3 按 `plan_run_id` 合并聚合、单 Run 单聚合者、读 Job 事实重算、at-least-once 幂等；D4 chain/dedup/通知/报告在父终态提交后且具重复执行保护（含「聚合已提交、投递失败」恢复矩阵）；D5 停写 ACK + 历史读兼容；**D6 post_completion 独立队列/Worker 作为独立 Decision，允许「D1–D5 接受、D6 延后」**。
3. **状态保持 Proposed**，并把 Proposed→Accepted 的 6 条门槛（热行写入有界 / p99 明显低于 1.1s 且不靠放宽舱壁 / 120s 收敛与计数一致 / drift·500·53300·timeout 全 0 / 副作用无重复丢失 / 回滚与 reconciler 恢复明确）写成该 ADR 的 §5——**真机数据是门槛，不是本稿**。
4. 归属域写 `semantic-ownership plan-run-scaling`：本稿是 ADR-0026（该 key 的 owner 文档）§6 的直接替代者；转 Accepted 时若需改表锚，按 S15 流程只改一行。
5. 交付 [`2026-09-24-capacity-p0-rollout-runbook.md`](../../operations/2026-09-24-capacity-p0-rollout-runbook.md)：含**只读实测的现状快照**（控制面已在 `c371113` 上跑 #3241/#3249 代码但 unit 无门禁、告警副本未同步、#3251 未分发、当前有 480 RUNNING 在跑）、五步执行清单（unit 门禁 + start-limit / 规则副本 / Agent 分发 A/B 两条路径 / 真机复跑观测 / 归档 #3244）、回滚与「本窗不做」。

## Alternatives

| 备选 | 结论 | 理由 |
|---|---|---|
| 直接修订 ADR-0026 §6 正文 | 驳回 | 历史决定不可改写；且 ADR-0026 还有 §1–§5、§7 仍生效，改写正文会牵连无关条款。新建 + 表格化「替代/继承」是既有先例（ADR-0051 对 0039/0046/0033） |
| ADR 与实施同一个 PR | 驳回 | owner 明确「先 ADR 后实现」，且转 Accepted 的前置是真机数据；捆绑会让「Proposed 文档」与「已上线行为」错位 |
| 手册以 issue 评论交付 | 驳回 | 窗口执行需要可复核、可 diff 的载体；评论随信息流淹没，且回滚命令需要与现状快照同文 |
| pending 用「每 Run 共享行的 pending 计数」 | 驳回 | 只是把 `plan_run` 热行换个名字；insert-only 的 per-job 行不产生共享行读改写 |
| 聚合改事件增量（delta）计账 | 不采（记为备选 C） | 引入第二计数源，与 Job 事实可漂移；重算与既有 `recount_plan_run_counters` 口径逐字一致 |
| 调大舱壁 / 抬 `max_connections` 替代解耦 | 驳回 | #3243 已测出 p50 由舱壁等待构成；调大只会把排队从舱壁搬回行锁 |

## Verification

- `python tools/dev/check_governance_surface.py` → **OK（S1–S15）**：ADR-0052 的头部状态行 ↔ `adr/README` 主表 ↔ M7 看板 ↔ `DOC-MAP` 四处一致，版本记录块与头部 v0.1 一致。
- `python scripts/run_gates.py check:quick` → **OK**（文档 PR 的常规门禁面）。
- 手册 §0 的现状快照全部是**只读实测**（`systemctl show/cat`、`readlink`、`promtool`/规则 API、`audit_logs`、`job_instance`、`.env.backend` grep、`check-monitoring-assets.py`），命令与输出已附在手册 §5，可逐条复核。
- **未执行（不得读成已验证）**：生产部署（unit/规则/机队分发）、真机复跑观测；两者都在手册里标为「未执行/本窗做」，其达标结论留给 owner 与 #3244。

## Revisit

1. **转 Accepted 的唯一通道**：部署窗按手册执行 → 真机复跑 6 条达标 → owner 裁决（owner 原话：若真实 p99/锁等待/体验已满足目标，**#3244 可延后甚至不实施**）。
2. 实施前的开放问题（ADR-0052 §7）：批次窗口与批量上限的「界」、pending 保留策略、D6 Worker 形态、pending 表命名与迁移。
3. 手册的两处窗口前提可能过期（在跑 run 的数量、`current` 指向的变化）；执行前以手册 §0 命令重采一次为准。
4. 若 #3244 实施，需同步核对：`ADR-0012:47` 的「post_completion 与终态同事务」措辞 vs 现行 commit 后入队；以及 `agent_coordinator_heartbeat.py:116` 对 `_bump_host_counters` 的注释引用。
