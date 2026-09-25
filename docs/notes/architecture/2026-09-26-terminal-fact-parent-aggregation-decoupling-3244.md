# ADR-0052 D1–D5 实施：终态事实与父 Run 聚合解耦（#3244）

Status: implemented
Class: architecture

关联：[#3244](https://github.com/DUElost/stability-test-platform/issues/3244)（实施单）、
[ADR-0052 v1.1](../../adr/ADR-0052-terminal-fact-parent-aggregation-decoupling.md)（裁决本体：
D1–D5 Accepted、D6 不采纳、§7 定值）、
[#2959](https://github.com/DUElost/stability-test-platform/issues/2959)（父单：容量 P0/P1）、
[#3299](https://github.com/DUElost/stability-test-platform/issues/3299)/PR #3307（编排者结构前置）、
[ADR-0026 §6](../../adr/ADR-0026-plan-execution-scaling.md)（被替代两处已标注）。

## Decision

按 ADR-0052 v1.1 的 D1–D5 落地「Job 终态事务不写父级热行」，实施 PR 未动 §7-1 数字
（批 500 / 帽 50 保持初值，调数须记录依据——本轮无压测调数需求）：

1. **D1** `job_terminalization.on_job_terminal(_sync)`：终态事务只插
   `plan_run_pending_aggregation` 一行（insert-only，`(plan_run_id, job_id)` 复合主键
   + `ON CONFLICT DO NOTHING` ⇒ outbox 重放幂等），**自管理提交**保持 #986/#1172/#2531/
   #2635/#2787 的全部边界契约（「一候选一提交点」由服务自身成立，不再依赖 applied）。
   不再锁 `plan_run`、不再 bump 五列、不再写 ACK。
2. **D2** 提交后唤醒：生产 SAQ `aggregate_plan_run_task`、`key="agg:{plan_run_id}"`
   （按 key 去重 ⇒ 终态波合并成一次聚合）；入队失败只告警，事实不丢。
   `TESTING=1` 直接 `asyncio.to_thread(drain_plan_run_aggregation_sync, …)` 内联排空
   （等价旧「终态即聚合」，存量测试面不漂移）。
3. **D3** 聚合执行器住 `plan_run_finalization`（§8 指定落点）：每轮独立事务
   锁父行（FOR NO KEY UPDATE）→ 取 ≤500 标记 → 读 `job_instance` 事实**重算**
   run/host 计数（无第二计数源；per-host 投影重算顺带补上 reconciler 原本不修
   host 列的缺口）→ **同事务**删除已消费标记（§7-2 消费即删）→ applied 判定沿用
   `_TERMINAL_PLAN_RUN_STATUSES` 守卫。排空循环到「本轮取 0」才退出（§7-1 必需项），
   安全帽 `STP_AGGREGATION_DRAIN_MAX_ROUNDS=50`（防单任务无限驻留，余量归恢复路径）。
4. **D4** 重复执行保护：`_finalize_plan_run` 与父终态**同事务**写
   `plan_run.terminal_effects_state='pending'`；副作用块（announce → commit → chain →
   dedup）走完置 `'done'`（`finalize_parent_run_*` 尾部，独立提交）。补偿扫描住
   counter_reconciler（leader sweep，300s）双通道：(a) pending 积压 → 内联排空；
   (b) `terminal_effects_state='pending'` 残留 → 经编排者重放副作用块（chain CAS /
   dedup key / done 标记三层拦住重复）。abort 批量与 reconciler 直发路径在自身
   announce 后同事务置 done（不留下会被重放的假阳性）。
5. **D5** ACK 逐 Job 写从 `agent_completion` 删除；abort 入口的初始空数组与保留合并
   不动；历史 JSON 不动；前端类型声明保留（读兼容）。

配套：迁移 `d4e8f2a7c9b1`（建表 + 加列 + 偏索引，单 head）；观测面
`stability_plan_run_pending_aggregation_depth` Gauge（reconciler 每轮 set）与
`stability_plan_run_aggregation_replayed_total{kind}` Counter（恢复路径触发次数，
验收后应恒 0）；ADR-0012 「post_completion 同事务生成」措辞订正（§3 顺带项）；
ADR-0052 §8 回注实施状态。

## Alternatives

- **asyncio 镜像执行器（async 版 round/drain 各写一遍）**：否决——双份实现必漂移；
  统一 sync 核心 + `asyncio.to_thread`（SAQ 任务与 TESTING 路径同形），与
  `post_completion_task` 先例一致。
- **D4 标记放 run_context JSON**：否决——与 abort 的 `abort_requested` 读改写共享
  一个 JSON 列，回到「父行 JSON 读改写」的形态；独立列 + 偏索引只扫 pending 行。
- **TESTING 下走真实 SAQ worker 等待聚合**：否决——测试确定性崩塌（worker 时序），
  且多数存量断言依赖「终态即收敛」。内联排空保持旧语义，唤醒路径本身由新守卫的
  非 TESTING 用例（enqueue 形状断言）覆盖。
- **reconciler 重放路径也触发 chain/dedup（正常编排）**：已如此——重放即经
  `finalize_parent_run_sync` 全块；仅 reconciler 主循环的 re-aggregation 保持旧形状
  （announce + done，链归 post_completion 的 recover_chain_trigger），不扩大本单行为面。
- **排空循环不设安全帽**：否决——§7-1 只要求「排空到空」，未授权单任务无上界；
  病态 Run（持续终态流）会让 SAQ 任务无限驻留占 worker 槽位。帽=50 轮 × 500 行
  = 25000 事件 ≫ 最大已知波（490），余量由 sweep 兜底。

## Verification

已跑：

- `pytest backend/tests`（api/services/scheduler/integration/core 全量）——见 PR 描述回填；
  定向轮实测：services+integration 1337 passed、api 1311 passed。
- `pytest tests/`（根结构守卫）：lock-order / inner-import 棘轮 / env_inventory
  三处本单触红，按判据纪律处置：棘轮基线 597→620 留痕（执行器局部 import 是
  编排模块「顶层只取 models 纯定义」纪律的既有形态，依赖方向无环）；
  两个新 env 键登记进 `backend/.env.example` + `env_inventory.py --write`；
  drain 轮次循环登记 `_ADJUDICATED_SAFE`（迭代对象是轮数上界、非取锁集合）。
- §6 回滚演练（一次性 postgres:16 容器，隔离库）：`alembic upgrade head`（终态
  d4e8f2a7c9b1，表/列存在）→ `downgrade -1`（表/列=0）→ `upgrade head` 幂等复建 →
  单 head 确认。回滚代码面 = revert 本 PR：pending 表停用无需数据迁移（计数是派生
  数据，reconciler recount 自愈）、`terminal_effects_state` 列可留（NULL=从不重放）、
  历史 ACK JSON 未动。
- 新守卫 `backend/tests/services/test_terminal_aggregation_decoupling_3244.py`：
  D1/D5 静态（AST 去 docstring 判禁）、D3 排空多批 + 安全帽余量留存、
  D4 双通道恢复各「重放一次、done 后不重复」。
- `scripts/run_gates.py check:quick`：见 PR 描述回填。

未做（诚实标注）：§5 六条实施验收需**部署后**按 runbook Step 4 同口径真机中止复跑
（基线 = plan_run 556 的 `/complete` p99 2.467s），结果回贴 #3244——本 PR 不声明已达标。

## Revisit

- `STP_AGGREGATION_BATCH_LIMIT` / `DRAIN_MAX_ROUNDS`：Step 4 复跑若显示单批未收口
  （`plan_run_aggregated` 日志 rounds>1 常态化）或 pending_depth 尖峰持续，按 §7-1
  在结构内调数并记 #3244。
- D6 复议触发器不变（`saq_queue_depth` 持续 >0 超 60s / 心跳·续租因槽位延迟）：
  触发即修订 ADR-0052，Worker 形态在重开修订内定（§7-3）。
- `terminal_effects_state` 历史列清理（成功 run 恒 done 累积）：暂无成本，若索引
  膨胀再议（偏索引只覆盖 pending 行）。
- TESTING 内联与生产异步唤醒的行为差：若出现「只在生产出的聚合时序 bug」，
  考虑给测试加一条非 TESTING 的 drain-单测通道（本轮已有 enqueue 形状断言打底）。
