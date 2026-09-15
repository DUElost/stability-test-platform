# 死锁问题面收口：abort phase-2 预锁续持 + recovery_sync 锁序对齐（#2012 / #2015）

Status: implemented
Class: bug-fix
Issues: #2012 (P1)、#2015 (P2)

## Decision

两处共享行加锁反向，均按 `docs/notes/architecture/2026-09-14-shared-row-lock-table.md`
的 I1/I2 基准对齐；背景是 09-15 17:00–19:47 生产负载恢复后死锁复现 60 次，PG 服务端
日志定位到环侧已从回收器移到 `recovery_sync`（#1959/#1980/#1985/#2022 修的是其余各边）。

1. **#2012（`abort_plan_run` phase 2）**：#1985 的「先按 id 预锁候选 PENDING job 行、
   再锁 plan_run」只覆盖了函数中部 `#703` commit **之前**的分段——COMMIT 把预锁一并
   释放，之后「重锁 plan_run → `_bulk_abort_pending_jobs` UPDATE job」又回到
   plan_run → job 反序。修复：在 commit 之后、重锁 plan_run 之前，对同一批
   `pending_ids` 重发同序预锁（`id.in_(pending_ids)` 按 id 升序 `FOR UPDATE`），
   使**全函数**保持 job → plan_run。锁面与开头预锁相同（含已离开 PENDING 的行，
   批量 UPDATE 的 `WHERE status='PENDING'` 照常跳过），持锁窗口到 finalize commit。

2. **#2012 连带（`abort_jobs_for_host` 跳过分支补 `db.rollback()`）**：该 except 把
   `PlanRunAbortError` 当「可跳过」继续处理同 host 其余 run，且同一 session 随后被
   热更新流程复用（drain 轮询 + SSH 部署，分钟级）——异常路径已取得的 PENDING/plan_run
   行锁会带满整个窗口，阻塞 complete_job / recycler（claim 的 SKIP LOCKED 会静默让路，
   #2104 的等待观测面也看不到）。回滚释放行锁；`plan_run_ids` 在循环前已物化，
   回滚对后续迭代无影响。

3. **#2015（`recovery_sync`）**：把 active-jobs 循环里的两条 SELECT 交换次序——先
   `JobInstance FOR UPDATE`，再 `DeviceLease FOR UPDATE`（Device 仍在其后），与 I1
   基准（complete_job / extend_leases_batch / reconciler 的 Job → Lease）同序。
   全部校验（ownership/fencing/boot）与动作判定不动：`lease is None` 分支的
   ABORT_LOCAL 结论与原先一致（差异仅是该分支现在多持有一条 job 行锁直到事务结束）。

4. **锁序表回填**：I1 点表补 `recovery_sync` 行、I2 的 `abort_plan_run` 行收窄为
   「全函数（含 phase-2 重发预锁）」、Revisit 增补「本表由人工枚举维护，新增写者须
   合入前回填」条款（#2015 的教训：表自称「全量」时，缺行会被下一个使用者当成
   「已核对」）、历史表补 #2012/#2015 两行。

5. **ci.yml 接线**：新增 `backend/tests/api/test_recovery_sync_lock_order_2015.py`
   （文件名含 `lock_order`，被 `tests/test_lock_order_pr_path_contract.py` 第一档
   发现式守卫要求进 PR 路径），加入 `pr-migrate-empty-db` 的并发回归 pytest 命令。

## Alternatives

- **终态探测前置（#2012 评论区建议的连带 1：把「不加锁的存在性/终态探测」提到预锁
  之前）——部分采纳**：只保留 except 回滚，**不做**终态探测。原因：`PLAN_RUN_VALID_
  TRANSITIONS` 里 `FAILED → QUEUED` 是合法迁移（V2-only requeue），PlanRun **不是
  单调趋于终态**的——预锁前看到 FAILED 就 raise，会在「探测后、锁内复检前」撞上并发
  re-queue 时改变行为（旧代码会继续 abort 该 re-queued run，新代码会跳过它，热更新
  放走一个活 run）。except 回滚已完整封闭锁泄漏（对任何 raise 位点成立），终态探测
  的增益只剩省两次注定失败的取锁，不值得引入语义风险。
- **豁免登记（#2015 的修复方向 2：把 recovery_sync 登记为已接受的反向）**：否决。
  生产 PG 日志已实证该环可触发（09-15 复现 60 次），「接受」不成立。
- **recovery_sync 按 job_id 排序 entries**：不做。环的成因是 (job, lease) **对内**
  次序；跨 entry 无共享行（job 归属唯一 host，active lease 每设备一条），同 host 并发
  recovery 由 Agent 串行化。保持 payload 顺序，动作序不变。
- **把 #2015 回归并进 `test_shared_row_lock_order_1980.py`**：否决，独立成
  `test_recovery_sync_lock_order_2015.py`——按命名判据被守卫发现并强制接线，
  charter 不与 #1980 混装。

## Verification

- 新增 PG 并发回归（第三会话 `FOR UPDATE NOWAIT` 探测 + `pg_stat_activity` 等待
  采样，判据沿用 #1959/#1980）：
  - `test_abort_lock_order_1985.py::test_abort_relocks_pending_jobs_after_commit_
    before_plan_run`：blocker 在 abort 首次 commit **之后**占住 plan_run 行（用
    `record_plan_run_abort_lock_seconds` 的 `abort_requested` 阶段作同步闸门），
    探测此刻 abort 已持有 PENDING job 行锁。反事实：stash 修复后该测试以
    「phase 2 仍是 plan_run→job 反序」失败；恢复修复后通过。
  - `test_recovery_sync_lock_order_2015.py`：blocker 持 Job 行时，探测 recovery_sync
    未持 lease 锁。反事实：stash 修复后以「锁序仍是 Lease→Job（55P03）」失败；
    恢复修复后通过（NOOP 收敛、租约未动）。
- **测试自身的一个坑（对后续并发回归有用）**：SQLAlchemy 2.0 的 `Session.commit()`
  把连接归还池子——abort 首次 commit 后，后续语句可能在**另一个后端连接**上执行，
  线程启动时快照的 `pg_backend_pid()` 会失配（首次实现因此盯错了会话，20s 轮询恒
  False）。修正：在闸门放行后、预锁执行前，在 abort 会话内**重新**取当次 pid；
  其后到被阻塞点之间不再有 commit，pid 保持有效。
- 全套锁序回归同库同会话：abort 1985 ×2 + 1980 ×2 + recovery 2015 + reconciler 1959
  + retention 2022 + lock-wait 2104 + 接线守卫 = **18 passed**。
- 覆盖两函数的既有功能面：test_plan_run_abort、test_plan_run_abort_api、
  test_plan_run_abort_aggregator_race、test_host_upgrade_gate、test_agent_dual_write
  （recovery/abort 行为不变性）实跑结果见 PR 描述。
- `python scripts/run_gates.py check:quick`：见 PR 描述。

## Revisit

- **#2012 的残余等待面**：全函数 job → plan_run 后，abort 与 recycler PENDING 超时
  路径在共享行上从「死锁」变为「排队」，代价对死锁计数不可见——按锁序表 Revisit 的
  口径看 `stability_db_lock_waiters` / `stability_db_lock_wait_max_seconds`
  （#2104），不要只看 `stability_db_deadlock_total` 归零。
- **phase-2 预锁的锁窗**：从「#703 commit」延长到「finalize commit」（多覆盖批量
  UPDATE 的窗口）。若观测面显示该窗口排队显著，杠杆是给 recycler 的 PENDING 超时
  候选做同序预锁（它现在必然等 abort 完成），而不是回退本修复。
- **锁序表的「全量」语义**：本次再次证明人工枚举会漏（recovery_sync 自 ADR-0019
  Phase 3a 起就反向、首版枚举漏行）。Revisit 已加「新增写者须回填」条款；若下一轮
  审计再现漏行，考虑把「`with_for_update` 全仓扫描 ↔ 点表比对」做成门禁而非审查项。
- **recovery_sync 的 `lease is None` 分支现在多持一条 job 行锁**（直到请求结束）：
  该分支对应「Agent 上报了租约已不存在的 job」的陈旧上报，job 行多为终态，无热路径
  争用；若观测面出现该路径的等待，再把存在性探测挪回锁前。
