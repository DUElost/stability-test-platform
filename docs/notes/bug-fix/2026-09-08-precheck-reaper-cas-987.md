# R06-F02 落地：PRECHECK 回收器加锁复读 + 条件更新（#987）

Status: implemented
Class: bug-fix

## Decision

`reconcile_stale_precheck_v2` 原先无锁扫描后直接按快照对象 `transition` 并
`commit`，是 admission 队列四条写路径（`claim_queued_plan_runs` /
`admission_transaction` / `requeue_plan_run` / `fail_plan_run_admission`）中
唯一不加行锁、不做所有权检查的。窗口内 admission 提交 RUNNING（已物化
Jobs）或用户 abort 提交 FAILED 时，reaper 用旧视图写回 QUEUED，产生
「QUEUED 却已有活跃 Jobs」或覆盖带 `abort_requested` 的 `run_context`。

修复（`backend/scheduler/precheck_reaper.py`）：

1. **扫描保持无锁**（候选查询只取 `id`，按 `id` 升序），单行处理下沉为
   `_recover_stale_precheck_run(db, run_id, stale_deadline, now)`；
2. **写前锁复读**：`SELECT ... FOR UPDATE` + `populate_existing`（防同一
   Session identity map 命中旧属性），复查完整 stale 谓词——status 仍为
   PRECHECK、`precheck_started_at` 非空且仍早于 deadline；
3. **attempt 所有权由谓词隐含表达**：PRECHECK 行不存在「原地刷新不换状态」
   的合法路径——claim 重试必同时改写 `started_at` 与新 attempt，任何
   attempt 旋转都伴随状态离开 PRECHECK 或 started_at 变新，故
   status+started_at 复查即等价于 attempt 所有权一致；显式比较快照
   attempt 无独立信息量；
4. 校验失败 → `rollback()` + `skipped`（对齐 `requeue_plan_run` /
   `fail_plan_run_admission` 非 owner 分支的收尾风格），主循环照常计数。

加锁后两个方向都收敛：admission/abort 先提交 → reaper 复读见新状态 skip；
reaper 先回收（attempt 置空）→ admission 端既有 CAS 使其 no-op（既有
`test_stale_attempt_noops` 覆盖）。双 reaper 重叠 tick 由行锁串行，后到者
复读见 QUEUED/FAILED 即 skip，attempt 计数不重复。

## Alternatives

- **初查即 `FOR UPDATE SKIP LOCKED` 整批锁行**——放弃：回收是低频后台
  tick，整批长持锁放大 admission 等待；单行「复读」窗口只有一条 SELECT
  距离，锁面最小；
- **条件 UPDATE（`UPDATE ... WHERE status='PRECHECK' AND ...`）绕过 ORM**——
  放弃：需为 `run_context`/`result_summary` JSON 修改另写原生 SQL 或加
  version 列；FOR UPDATE 复读与同文件其余写路径模式一致，实现内聚；
- **初查快照 attempt_id、复读显式比较**——放弃：见 Decision §3，谓词已
  覆盖全部旋转路径，显式比较冗余。

## Verification

- **反例实证**：回退实现文件（`git checkout HEAD~1 --`，测试保留）跑新增
  `TestReaperCompetition` 4 用例——修复前 **4 failed**；修复版全绿；
- 新增用例（`test_admission_queue_step4.py::TestReaperCompetition`）：
  admission 先提交 RUNNING → reaper noop 且 3 jobs 保留；abort 先提交
  FAILED → reaper noop 且 `abort_requested` 完整保全；双 reaper tick 不
  重复计数（attempts 恒 1）；`threading.Barrier` 真实并发（独立
  `SessionLocal`）端到端——终态只 ∈ {RUNNING+3jobs, QUEUED+0jobs}；
- step2 + step4 全套 **63 passed**（含既有 reaper 黑盒行为回归）；
  `backend/tests/scheduler/` 全套 **50 passed**（v1 reaper 不受影响）；
- `check:quick` 与 PR 门禁：见 PR 描述。

## Revisit

- 同文件在案 #797（v2 盲重排不查 SAQ job/worker 存活）与 #987 不同根因
  （#987 管「写时复查 DB 状态」，#797 管「候选资格」），未并入本单；
- 若未来出现「保持 PRECHECK 原地刷新 started_at」的新路径，本谓词需升级
  为显式 attempt 快照比较（Decision §3 的前提届时失效）；
- `singleton=True` 仅约束单进程内调度，跨实例重叠 tick 由本次行锁兜底
  （#890 领导选举 fail-open 独立在案）。
