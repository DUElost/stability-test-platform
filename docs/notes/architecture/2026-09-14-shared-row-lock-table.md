# 共享行加锁表（首版）

Status: implemented
Class: architecture

## Decision

把 `docs/reviews/PROJECT_REVIEW_PLAN.md` §3 第 7 条（「以共享行 / 资源为单位穷尽列出全部
加锁点及其顺序，并断言全局存在一致的全序；出现相反顺序即判 P1」）落成一张**常驻表**。
后续轮次按「增量核对新增加锁点」使用，不必重新枚举。

枚举单位是共享行，不是业务路径：路径分区天然看不到环（同一批行被两条路径以相反顺序加锁
时，每条路径在自己的区里都自洽）。这是 2026-09 那轮 R01–R15 全面审查漏掉
`job_instance` / `device_leases` 锁序环的方法层原因（另见 #1960）。

### 四条已接受的不变量

| ID | 不变量 | 主要承载路径 |
|---|---|---|
| **I1** | `job_instance` → `device_leases` | `complete_job`、`extend_leases_batch`、`_reconcile_expired_leases`、`extend_job_lock` |
| **I2** | `job_instance` → `plan_run` | 同上（终态化时）、recycler 超时路径 |
| **I3** | `job_instance` → `plan_run_host` | 终态化 `_bump_host_counters`、`coordinator_heartbeat`、`_bulk_abort_pending_jobs` |
| **I4** | `plan_run` → `plan_run_host` | `on_job_terminal(_sync)`、`admission_transaction`、`_bulk_abort_pending_jobs`、`prepare_plan_run` |

**豁免**（形式相反但不构成反序）：

- `admission_transaction`：`plan_run` → `plan_run_host` → `host` → `device` → `job`，
  其中 job 侧是 **INSERT 新行**（不取既有行锁）；且 prh 已按 `host_id` 定序
  （`_lock_admission_resources` 同理按 id 升序），因此不需要满足 I1/I2/I3。
- `plan_dispatcher_sync.prepare_plan_run`：plan_run 与 prh 都是 INSERT 新行。
- `plan_run_manual`（RETRY_NOW / EXIT_REQUESTED）：只改 job 行，不取 plan_run 锁。
- `host_retirement` / `host_maintenance`：只取 `host` 行锁，对 job/prh 仅只读 count。

### 全量加锁点表

**`job_instance` × `device_leases`（I1）**

| 路径 | 顺序 | 位置 |
|---|---|---|
| `complete_job` | Job → Lease | `agent_api.py`：Job `FOR UPDATE` → `release_lease` |
| `extend_leases_batch` → `_cas_renew_leases` | Job → Lease | Job `ORDER BY id FOR UPDATE` → CAS `UPDATE device_leases` |
| `extend_job_lock` | Job → Lease | `#1980` 修正为 Job `FOR UPDATE` → `extend_lease` |
| `_reconcile_expired_leases` / `_reconcile_stale_unknown_jobs` | Job → Lease | `#1959` 修正 |
| `recycler` PENDING/RUNNING 超时 | Job → Lease | 逐 job savepoint → `release_lease` |
| `acquire_lease` / `claim` | Job → Host → Lease | 不取 plan_run |

**`plan_run` × `job_instance`（I2）**

| 路径 | 顺序 | 位置 |
|---|---|---|
| `complete_job` / `recycler` / reconciler / `coordinator_heartbeat` | job → plan_run | 基准 |
| `abort_plan_run` | job → plan_run | `#1985` 修正（先按 id 预锁候选 PENDING 行，再锁 plan_run） |
| `admission_transaction` | plan_run → job(INSERT) | 豁免（新行） |

**`plan_run_host` × `job_instance`（I3）**

| 路径 | 顺序 | 位置 |
|---|---|---|
| `complete_job` → `on_job_terminal` → `_bump_host_counters` | Job → PRH | 基准 |
| `_reconcile_expired_leases` / `_reconcile_stale_unknown_jobs` | Job → PRH | `#1959` |
| `recycler` → `plan_aggregator_sync` | Job → PRH | — |
| `coordinator_heartbeat` | Job → PRH | `#1980` 修正 |
| `_bulk_abort_pending_jobs` | Job → PRH | — |
| `admission_transaction` | PRH → Job(INSERT) | 豁免（新行 + prh 定序） |

`plan_run_host` 的写点全仓只有三处 ORM（`_bump_host_counters`、
`admission_transaction` 的 `:729-735`、`coordinator_heartbeat`）+ 一处批量
（`plan_run_abort:130`）。

**`plan_run` × `plan_run_host`（I4）**

| 路径 | 顺序 | 位置 |
|---|---|---|
| `on_job_terminal` / `on_job_terminal_sync` | plan_run → prh | `job_terminalization.py:140-146` → `:152-162` |
| `admission_transaction` | plan_run → prh | `admission_pump.py:632` → `:649` |
| `_bulk_abort_pending_jobs` | plan_run → prh | prh 更新在 `:130`，plan_run 锁更早 |
| `prepare_plan_run` | plan_run(INSERT) → prh(INSERT) | `plan_dispatcher_sync.py:755` → `:761` |
| `coordinator_heartbeat` | 只碰 prh | 不构成参与方 |

→ I4 无非参与方、无反向。

### 唯一的形态反向：夜间保留清理（登记为「接受，不修」）

`cron_scheduler.run_retention_cleanup` 在**一个事务**里：

1. `_retention_candidate_ids`（`cron_scheduler.py:264-297`）`SELECT PlanRun … FOR UPDATE
   SKIP LOCKED`（`:291`）——**先锁 plan_run**；
2. 再按 FK 子表顺序删除：`DeviceLease`（`:393`）→ … → `JobInstance`（`:418`）→
   `PlanRun`（`:421`）。

即 `plan_run → device_leases → job_instance`，与 I1/I2 形态相反。**决定：本版不修**，理由与
判据见下（并写进 Revisit，附「将来要修时的正确形状」）。

候选过滤是「`SUCCESS`/`FAILED`/`PARTIAL_SUCCESS` 且 `started_at < now -
plan_run_retention_days` 且未被链引用」——必须是**已终态且超过保留期**的 run。要成环还需该
run 的 job / 租约行同时被热路径持有：

- recycler 的超时路径需要「已终态 run 下的非终态 job」并存活数十天（PENDING 超时是 120s、
  RUNNING 有 heartbeat 时钟，正常几十分钟内处理掉）；
- reconciler 对**终态 job** 走「D5 终态租约释放」分支，只释放租约、不调 `on_job_terminal`，
  整个事务不取 plan_run 锁。

故只可能出现短暂等待。**与已修项的分界是重叠窗口**：#1985 修的 `abort_plan_run` 与热路径
争用同一批行、亚秒级交错；这里的重叠窗口是**天数级共存**。引用本表时不要只看「顺序是否
相反」——形态相同、窗口不同的两项处置相反，这是刻意的。

### 顺带登记（未改）

- **`released_leases` 恒为 0**：`plan_run_abort.py:379` 置 0 后从未自增，却被返回体与
  `abort_jobs_for_host` 的 docstring 承诺（现有 API 测试也断言 `== 0`）。租约释放实际由
  reconciler / recycler 承担。属「文档宣称 > 实现」残留。
- **保留清理的持锁时长**：同一事务内还做 NFS 目录删除（`purge_run_storage_dirs`，
  #1521/#1698 的「先文件后行」设计），plan_run 行锁被持有到文件操作之后。不是锁序问题，
  但会放大与热路径的等待时间。

## Alternatives

- **把表放进 `docs/design/` 成为设计契约**：本版放弃。`docs/design/` 的读者面向「系统如何
  工作」，而本表是审查方法的产物、随每次新增加锁点增量维护；放进 `docs/notes/` 与
  `#1960` §3 第 7 条的引用链更短。若下一轮出现第二份同类表，再评估合并进 `docs/design/`。
- **只写在 issue 评论里**：放弃。issue 不进仓库检索面，下一轮无法在本地增量核对——这正是
  R01–R15 那轮覆盖记录只留在 GitHub 台账、事后无法查证同一个坑。
- **顺带修保留清理的加锁顺序**：放弃（本版）。理由见 Decision 的重叠窗口分析；在
  「可达性 ≈ 0」且候选选择依赖 plan_run 锁与热路径互斥的前提下改锁序，风险大于收益。
  将来若保留期被调小到小时级，按 Revisit 给的形状改。
- **把 `released_leases` 一并删掉**：放弃。是行为/接口面变更，与「只登记事实」的本单不同类，
  应单独评估是否有外部消费者。
- **为保留清理补一条 PostgreSQL 回归**：放弃。要构造「终态 run + 存活数十天的非终态 job」
  才能命中，测试本身会变成不可信的人为场景；本表已登记可达性论证，够用。

## Verification

本表的来源与验证方式：

- **枚举依据**：逐文件读取全部写点与显式加锁点——
  `select(...).with_for_update` 全仓扫描、`update(JobInstance|PlanRunHost|PlanRun|DeviceLease)`
  全仓扫描、以及 ORM 属性赋值形态（不会出现在 `update(...)` 搜索里）逐个确认；
  `plan_run_host` 的写点经全仓确认只有 4 处（见 Decision）。
- **合入后同库同会话回归**：在 `origin/main`（含 `#1979`/`#1981`/`#1983`/`#1986`）上把三条
  锁序测试放同一 pytest 会话、同一数据库跑，确认互不干扰：

  ```
  pytest backend/tests/services/test_abort_lock_order_1985.py \
         backend/tests/api/test_shared_row_lock_order_1980.py \
         backend/tests/scheduler/test_reconciler_renew_lock_order.py -q
  → 5 passed（4.96s）
  ```

- **本单门禁**：`check_governance_surface.py --check` 全绿（首版曾因缺四节契约被 S10 拦下，
  已按契约重排）；`pytest tests/ -q` → 532 passed。
- **可达性论证的性质**：保留清理那一项是**静态可达性分析**（候选过滤 + 热路径时间窗），
  不是实测；本表据此判定「接受」，并把「若保留期被调小则需重评」写进 Revisit。未做的验证：
  未构造保留清理与热路径的真实并发交错（理由见 Alternatives）。

## Revisit

- **每轮增量核对**：任何**新增 / 修改**对 `job_instance` / `device_leases` / `plan_run` /
  `plan_run_host` 的写语句，先在本表定位该行，再核对该事务内**所有**相关行的加锁顺序是否
  满足 I1–I4；不必重新枚举全表。
- **回归护栏与其限度**：三条锁序测试都是 PostgreSQL-only（sqlite 下 skip），而 PR 阶段不跑
  `backend-test` —— 也就是说它们在合入门禁里**不会兜住**。若这类不变量需要真正的护栏，
  应把「锁序测试」并入 PR 阶段可跑的集合（独立裁决，见 `#1960` 的同类讨论）。
- **观测入口**：`stability_db_deadlock_total{engine}` 与告警 `StabilityDbDeadlockDetected`
  （`#1958`）。该计数器应长期为 0；出现增量即回到本表按行定位，而不是先怀疑语句形状。
- **保留清理**：若 `plan_run_retention_days` 被调小到小时级，或出现「保留清理 × 热路径」
  的真实等待/死锁证据，按此形状改：候选**无锁预读** → 按 id 升序锁 job 行 →
  再 `FOR UPDATE SKIP LOCKED` 复核终态 → 删除。**不要**把 deletes 挪到锁之前——候选选择
  本身依赖 plan_run 锁与热路径互斥。
- **`released_leases`**：若确认无外部消费者（前端/脚本/Agent），按「文档宣称 > 实现」清理
  该字段与其 docstring；在此之前不要把它当作租约已释放的信号。
- **表的位置**：若下一轮出现第二份同类表（或本表被别的文档大量引用），评估移入
  `docs/design/` 并同步 `docs/DOC-MAP.md`。

## 历史与关联

| 单 | 内容 |
|---|---|
| #992 | 发现 complete × extend-batch 半边（R06-F07），Revisit 预告了另一半 |
| #1959 | 回收器 Lease→Job 改为 Job→Lease + PG 并发回归 |
| #1980 | `coordinator_heartbeat`、`extend_job_lock` 两处反向 |
| #1985 | `abort_plan_run`（plan_run→job）改为 job→plan_run |
| #1960 | 把「以共享行为单位枚举」写进审查总纲 §3 第 7 条 |
| #1958 | 死锁指标与告警（本表的观测入口） |
