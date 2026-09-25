# ADR-0052：终态事实与父 Run 聚合解耦（Job 事务不写父级热行）

- 状态：**Proposed** v0.1（2026-09-24 起草；**未裁决**——按 owner 口径，部署窗真机复跑达标后才转 Accepted，见 §5）
- 优先级：P1（#3243 校准显示：被**接纳**的 `/complete` p99≈1.1s，主项是父行串行段；P0（ADR-0047）治的是容量与过载语义，没拆这条热点）
- 目标里程碑：M7
- 日期：2026-09-24
- 决策者：owner（待裁决）；起草：平台研发组
- 归属域：semantic-ownership plan-run-scaling
- 标签：terminalization, aggregation, row-lock, idempotency, outbox, ADR-0026, #2959, #3244
- 关联：[#3244](https://github.com/DUElost/stability-test-platform/issues/3244)（实施单：**须 ADR 先行**，本稿是其第一步）
  / [#2959](https://github.com/DUElost/stability-test-platform/issues/2959)（父单：容量与过载 P0/P1）
  / [ADR-0026](./ADR-0026-plan-execution-scaling.md)（**本稿替代其 §6 的两处执行语义**，保留其余；见 §3）
  / [ADR-0048](./ADR-0048-execution-status-semantics-v2.md)（终态判定输入不变——本稿明确不动）
  / [ADR-0047](./ADR-0047-db-pool-and-connection-capacity.md)（P0 已裁决并落地：预算门禁 / 503 过载语义 / 终态舱壁）
  / [ADR-0012](./ADR-0012-post-completion-pipeline-jira-automation.md)（post_completion 契约；D6 是独立 Decision；其历史措辞差异见 §3 末）
  / [#3243 校准 Note](../notes/bug-fix/2026-09-24-abort-backflow-scale-3243.md)（本稿的量化依据）
- 版本记录：v0.1（2026-09-24）首次提出，D1–D6 待裁决；D6（post_completion 隔离）被显式设计为**可单独延后**

## 1. 背景

### 1.1 事实面（读 `main@b8e574d5` 得到，无推测）

| 事实 | 出处 |
|---|---|
| 每个 Job 终态时，在**同一事务**内锁父 Run 行并自增计数：`SELECT plan_run … FOR NO KEY UPDATE`（`key_share=True`）→ `_bump_counters` → `_bump_host_counters` | `backend/services/job_terminalization.py:135-175`（lock `:150`，计数 `:112/:124`） |
| ABORTED 分支**额外**一次父行读改写：把 job id 追加进 `run_context.abort_requested.acknowledged_job_ids`（同样 `FOR NO KEY UPDATE`） | `backend/services/agent_completion.py:438-453` |
| `acknowledged_job_ids` **全仓无消费方**：abort 入口只做初始空数组与保留合并；前端仅类型声明；reaper / 审计 / UI 均不读 | `plan_run_abort.py:445/534/566/577`、`frontend/src/utils/api/types.ts:1602`（grep 全量清单） |
| 父 Run 终态由**最后到达的那个 Job** 顺带判定：`terminal_job_count == total_job_count` 时 `apply_plan_run_aggregation_from_counters` → `_finalize_plan_run`（状态迁移 + `ended_at` + `result_summary` + 通知 + 报告缓存刷新调度） | `job_terminalization.py:175-181`、`plan_run_aggregation.py:167-204` |
| chain / dedup 已在**提交后**执行（#986 契约）：`_post_aggregation_side_effects_*` 先 `commit()` 再 `trigger_next_plan` / `enqueue_dedup_terminal_*` | `job_terminalization.py:66-100` |
| `post_completion_task` 逐 Job 入队（SAQ `key=pc:{job_id}`，commit 后），与主链**共用单队列** worker（`SAQ_CONCURRENCY` 默认 10） | `agent_completion.py:499-510`、`backend/tasks/saq_worker.py:52-53` |
| 对账 sweep 已存在且定位是修复路径：`counter_reconciler`（`lookback 48h / batch 200`，周期 300s，leader 选举内跑） | `backend/scheduler/counter_reconciler.py`、`backend/core/settings/scheduler.py:74/101-102` |

### 1.2 量化：P0 之后还剩什么

| 观测 | R523（真实现场，2026-09-23） | #3243（压测，P0 全量落地后） |
|---|---|---|
| `/complete` 放大系数 | **3.35**（1644 次 / 490 事实） | **1.14**（557 / 490） |
| 背压形态 | 1053×500 + 1401 条 53300 | **0×500、0×slots_exhausted**；67×503（舱壁削峰） |
| 池峰（async） | **86**（+ sync 12 ≥ 97 槽） | **17** / 预算 40 |
| 可响应性 | 会话失效、UI 卡顿（#492 形态） | 探针 p99 **51.8ms**（2026-09-25 更正：**仅 `/health`**——同轮 heartbeat 探针缺必填 `status`，每次 422、未计入；按真实心跳形状重测 heartbeat p99 ≈ 48ms，见 [#3247 Note](../notes/bug-fix/2026-09-25-nightly-red-and-vacuous-capacity-criteria-3247.md)） |
| 收敛 | —（被打断） | **5.4s**（预算 120s） |
| **被接纳的 `/complete`** | —（大量 500） | **p99≈1.1s、p50≈0.55s** |

`p50 ≈ 舱壁等待预算（500ms）`说明排队发生在**舱壁**而不是连接池；`p99` 的剩余项与代码一致——**每个终态仍要锁并改写同一条 `plan_run`**。因此 #3243 的裁决建议是「不要据此调舱壁」（调大只会让更多请求挤上行锁，见其 Note）。

结论：P0 把「连接需求」关进了舱壁与池预算；剩下的结构热点是**父级热行的串行段**——这正是本稿要拆的对象。

## 2. 决策

**总原则（两条不变量）**

1. **Job 的终态事实只在 Job 域内落库**；父 Run / PlanRunHost 的聚合计数是**派生数据**，只能由聚合者批量读写。
2. **聚合触发必须持久化**：Redis/SAQ 只负责唤醒传输；终态聚合的事实源永远是可重放的数据（Job 事实 + 持久化 pending 标记）。

### D1 父级聚合行移出 Job 终态事务

- `/complete`（及其它一切终态入口）的 Job 事务**只写**：Job 终态、StepTrace、审计、lease 释放、**durable pending 标记**（D2）。
- 该事务**不再**：锁 `plan_run` 行、自增 `plan_run` / `plan_run_host` 计数器、读改写 `acknowledged_job_ids`。
- 保留 ADR-0026 §6 的三件不变量：**单一 terminalization 入口**（所有终态入口仍必须经过集中服务）、**五列计数器语义**（判定输入见 ADR-0048 D1，不变）、**集中服务 + 低频对账 sweep 自愈**。
- 被替代的 ADR-0026 §6 原文两处：①「在终态事务内 `UPDATE plan_run SET terminal_job_count = …`（单行原子自增）」；②「每个 Job 都持 `FOR NO KEY UPDATE` 父行锁」。改为：父行只在**聚合批次**内持锁（D3）。

### D2 聚合触发：durable pending 标记 + 提交后唤醒

- Job 终态事务内，向**只插不改**的 pending 表写一行（`plan_run_id` + `job_id` 唯一键；insert-only——不在任何「每 Run 共享行」上做读改写，避免把热行换个名字）。
- 事务提交后向 SAQ 投递唤醒（`key=f"agg:{plan_run_id}"` 去重；Redis 仅传输）。投递失败/丢消息**不影响事实**：pending 行仍在，由恢复路径重放。
- `counter_reconciler`（300s）继续保留为**修复路径**，**不参与正常时延预算**；pending 的常规消费由聚合任务完成，扫描只兜底（见 D4 恢复矩阵）。

### D3 按 plan_run_id 合并聚合 + 幂等

- 聚合单位是 `plan_run_id`；**同一 Run 同时只能有一个有效聚合者**（聚合事务内锁父行，或等价 advisory lock）。
- 聚合一次**读取当前 Job 事实**（`job_instance` 按 status / host 分组），批量更新 Run/Host 计数；计数的事实源仍是 Job 表——**不引入第二计数源**。
- 允许 at-least-once：消费 pending 行与计数更新在同一事务；重复执行收益为 0（计数是**重算**而非增量；父终态有 `_TERMINAL_PLAN_RUN_STATUSES` 守卫）。
- 批次内 `terminal == total` 才触发父 Run 终态，沿用 `_resolve_plan_run_status` + `_finalize_plan_run`（ADR-0048 语义不动）。

### D4 终态副作用边界（chain / dedup / 通知 / 报告）

- chain trigger、dedup enqueue、PlanRun 通知、报告缓存刷新必须发生在**父 Run 终态提交之后**（延续 #986 的提交顺序契约）。
- 具备**重复执行保护**：现有守卫（终态守卫、chain settle、dedup 去重键）不足处，实现时把「副作用已执行」标记与父终态**同事务**落库。
- **「聚合已提交、Redis/worker 随后失败」的恢复路径**（本条的必答项）：pending 行未消费 ⇒ 唤醒丢失/worker 崩溃后由扫描重放；父终态已提交而副作用未跑 ⇒ 由同一恢复路径重放，且重放必须被上一条保护拦住（不重复通知、不重复链式触发）。

### D5 ACK 删除与兼容

- **停止新增** `acknowledged_job_ids`：删除 ABORTED 分支的逐 Job 追写（`agent_completion.py:438-453`）。ACK 语义改由 **Job 终态推导**。
- **读兼容**：abort 入口的初始空数组与既有合并逻辑保留；历史 JSON **不动**；确认 UI / 审计 / reaper 完全无依赖后再议历史数据清理（另单，不在实施里顺手做）。
- 本项是纯删除且收益明确（ABORTED 波里再省一次父行读改写），风险面已被「无消费方」封住（§1.1）。

### D6 post_completion 隔离（独立 Decision，允许单独延后）

- `post_completion_task` 走**独立队列 + 独立 Worker**（首轮并发 ≈4），不再与 heartbeat / lease / 终态主链共用同一 worker 槽位。
- SAQ 0.26.4 的 `priority` **仅 postgres broker 可用**（`.venv/lib/python3.13/site-packages/saq/job.py:113`），本平台是 Redis ⇒ 只能落成独立队列 / Worker，不能靠优先级。
- **允许的组合**：D1–D5 接受、D6 延后。两者风险面不同（D6 只影响后处理吞吐，不影响终态正确性），不做捆绑裁决。

## 3. 与 ADR-0026 §6 的替代关系

ADR-0026 §6（`docs/adr/ADR-0026-plan-execution-scaling.md:232`）的原文分四块，本稿**替代两块、继承两块**：

| ADR-0026 §6 原文要点 | 本稿处置 |
|---|---|
| 「每个 Job 终态时：在终态事务内 `UPDATE plan_run SET terminal_job_count = terminal_job_count + 1, ...`（单行原子自增）」 | **替代**（D1） |
| 「保留 `FOR NO KEY UPDATE` 串行化锁……只是把锁内的全量 SELECT 换成计数器读取」——即每 Job 持父行锁 | **替代**：锁保留，但只在聚合批次内持有（D1/D3） |
| 「当 `terminal_job_count == total_job_count` 时才触发一次 `apply_plan_run_aggregation` 做终态收敛 + chain trigger」 | **继承**：触发条件语义不变，改由聚合者在批次末尾判定（D3） |
| 「单一 terminalization 入口原则」+「集中服务（应用层）+ 低频对账 sweep 自愈」 | **继承**：入口与自愈机制不变，入口内不再写父级计数（D1/D2） |

- **ADR-0048 D1 不动**：判定输入仍是 `failed_only / aborted / abort_requested` 三个计数；本稿只改计数的**产生方式**，不改判定语义。
- **ADR-0012 历史措辞**：「post_completion 在 Job 终态与报告同事务生成」（`ADR-0012:47`）与现行 implement（`agent_completion.py:485→501`：commit 后入队）不一致——实施时顺带订正措辞（不属本稿裁决范围）。

## 4. 备选方案与权衡

| 方案 | 取向 | 结论 |
|---|---|---|
| A 保持现状 + 放宽容忍（调大舱壁 / 抬 `max_connections`） | 把 p99 换个位置，不改父行串行段 | **驳回**：#3243 已测出 p50 由舱壁等待构成，调大只会把排队从舱壁搬到行锁 |
| B 只删 `acknowledged_job_ids` | 治 ABORTED 分支的一半 | **驳回**：p50 主项是 D1 的「每 Job 父行锁 + 计数」，不是这条 ACK |
| C 事件增量表（delta events）替代「读事实重算」 | 聚合只加计数、不回读 Job 表 | **不采**：引入第二计数源，与 Job 事实可漂移；reconciler 的自愈语义复杂化。重算与既有 `recount_plan_run_counters` 口径逐字一致 |
| D 每 Job 独立 advisory lock / 分片计数器 | 缩小锁粒度 | **驳回**：锁数量回到 O(N)，热点只是换名字，还破坏「单行计数器」的读模型 |
| E DB trigger 自动聚合 | 天然全覆盖 | **沿用 ADR-0026 已否决结论**：写放大、难单测、与 `JobStateMachine` 的应用层治理相悖 |

## 5. Proposed → Accepted 的门槛（owner 2026-09-24 口径）

**先部署窗真机复跑，逐条核对；在真实 48 台数据回来前不转 Accepted、不开始实现。**

1. `plan_run` 热行写入从 ~490 次降到**有界批次**；
2. `/complete` p99 **明显低于**当前 ~1.1s，且**不靠放宽舱壁**；
3. 终态 120s 内收敛，计数与 PlanRunHost 一致；
4. counter drift、500、53300、pool timeout **全为 0**；
5. chain / dedup / post_completion 无重复、无丢失；
6. 回滚与 reconciler 恢复路径明确（见 §6）。

## 6. 回滚与恢复

- **回滚形态**：代码退回「逐 Job 计数」路径；pending 表停用（**不需要数据迁移**——计数是派生数据）；漂移由 `counter_reconciler` recount 自愈；历史 `acknowledged_job_ids` 保持原样。
- **部分失败矩阵**：

| 失败点 | 后果 | 恢复 |
|---|---|---|
| pending 标记插入失败 | Job 终态事务整体失败 | Agent outbox 重放（事实不丢）；终态不提交但可重试 |
| 唤醒丢失 / Redis 不可用 | 聚合延迟 | pending 扫描重放（D2/D4）；PlanRun 收敛晚几十秒，事实无损 |
| 聚合者崩溃 | 事务回滚 | 重放（至少一次 + 重算幂等） |
| 父终态已提交、副作用未跑 | 链式 / 去重 / 通知延迟 | 恢复路径重放；副作用须幂等（D4） |

- **观测面**：`plan_run` 行锁等待、pending 深度、聚合批次次数/耗时、`counter drift`、终态收敛时延。

## 7. 开放问题（实施前定，均不改变本稿不变量）

1. 批次窗口与批量上限默认值（「有界」的界——压测校准后填数）。
2. pending 行「消费即删」vs「保留视界」（重建 / 审计需求）。
3. D6 独立 Worker 的形态（进程内第二 worker vs 独立进程）。
4. pending 表的命名与迁移（单数表名，随实施 PR + alembic 落地）。
