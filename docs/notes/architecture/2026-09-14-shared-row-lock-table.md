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
| `run_retention_cleanup`（`#2022` 修正） | Job → Lease → plan_run | 原为 `plan_run → Lease/Job`，见下 |

**`plan_run` × `job_instance`（I2）**

| 路径 | 顺序 | 位置 |
|---|---|---|
| `complete_job` / `recycler` / reconciler / `coordinator_heartbeat` | job → plan_run | 基准 |
| `abort_plan_run` | job → plan_run | `#1985` 修正（先按 id 预锁候选 PENDING 行，再锁 plan_run） |
| `run_retention_cleanup`（`#2022` 修正） | job → plan_run | 先预锁子树（job → lease）再锁 plan_run |
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

### 曾登记为「接受，不修」的形态反向：夜间保留清理（**#2022 已修**）

`cron_scheduler.run_retention_cleanup` **原先**在**一个事务**里：

1. `_retention_candidate_ids` 里 `SELECT PlanRun … FOR UPDATE SKIP LOCKED`——**先锁
   plan_run**；
2. 再按 FK 子表顺序删除：`DeviceLease` → … → `JobInstance` → `PlanRun`。

即 `plan_run → device_leases → job_instance`，与 I1/I2 形态相反。

**本表首版把它判为「接受，不修」，而那个论证的前提是错的**（#2022 事实更正）：

- 原写「需存活**数十天**」——实际 `plan_run_retention_days` 默认 **3 天**
  （`backend/core/settings/scheduler.py:62`；ADR-0038 亦载明「终态 Run 默认 3 天」）；
- 原假定「只可能短暂等待」——而保留清理在**持有 plan_run 行锁期间**还要做 NFS 目录回收
  （`purge_run_storage_dirs`，#1521/#1698 的「先文件后行」），持锁窗口是**秒级**而非毫秒级；
- 成环只需「终态 run 里的 job 被迟到 / 重复 `/complete` 命中」，而这类形态在本仓是**已知
  现象**（`#743` 幽灵 `/complete` 家族）。

判据更正为：**重叠窗口 = 保留清理事务持锁时长 × 该 run 的 job 被热路径触碰的概率**，
与「共存多久」无关。结论随之反转 —— `#2022` 已修：取锁顺序改为
**job → lease（`_retention_prelock_subtree`）→ plan_run（`_retention_lock_runs`）→ 删除**，
满足 I1/I2；候选选择改为**只读**，锁内复核终态与年龄（`SKIP LOCKED` 保留互斥语义），
删除内容与业务语义不变。

**本表的自我更正教训**：「顺序是否相反」与「重叠窗口是否够大」是两个独立判据，后者必须取
代码里的**真实数值**（保留期、事务持锁区间），不能凭印象填「数十天」。引用本表时，
看到「形态反向但接受」的说法必须能追到具体数值。

### 顺带登记（未改）

- **`released_leases` 恒为 0（已由 `#2089` 删除）**：原先 `plan_run_abort.py` 置 0 后从未
  自增，却被返回体、审计 details、日志与 `abort_jobs_for_host` 的 docstring 承诺，前端类型
  也照抄了这个键。租约释放实际由 reconciler / recycler 承担。确认全仓无消费者后，两侧一起
  删除（后端 + `types.ts`）。
- **保留清理的持锁时长（#2022 未改）**：同一事务内还做 NFS 目录删除
  （`purge_run_storage_dirs`），plan_run 行锁被持有到文件操作之后。#2022 只统一了**顺序**，
  没有缩短持锁时长——见 Revisit。

## Alternatives

- **把表放进 `docs/design/` 成为设计契约**：本版放弃。`docs/design/` 的读者面向「系统如何
  工作」，而本表是审查方法的产物、随每次新增加锁点增量维护；放进 `docs/notes/` 与
  `#1960` §3 第 7 条的引用链更短。若下一轮出现第二份同类表，再评估合并进 `docs/design/`。
- **只写在 issue 评论里**：放弃。issue 不进仓库检索面，下一轮无法在本地增量核对——这正是
  R01–R15 那轮覆盖记录只留在 GitHub 台账、事后无法查证同一个坑。
- ~~**顺带修保留清理的加锁顺序**：放弃（本版），理由是「可达性 ≈ 0」。~~
  **已被 `#2022` 推翻**：该理由的前提「保留期数十天」不成立（见 Decision），故按本表原
  Revisit 给的形状（预锁子树 → 再锁 plan_run）修复，并补了 PostgreSQL 回归。
- ~~**把 `released_leases` 一并删掉**：放弃。是行为/接口面变更，与「只登记事实」的本单不同类，
  应单独评估是否有外部消费者。~~ **已由 `#2089` 执行**：确认「无消费者读取」后两侧一起删
  （后端返回体 / 审计 details / 日志 + 前端 `types.ts`），并按 `#787` 的「后端为权威、
  前端类型跟随」纪律保持两侧一致。
- ~~**为保留清理补一条 PostgreSQL 回归**：放弃，理由是「要构造存活数十天的非终态 job」。~~
  **已被 `#2022` 推翻**：不需要那种场景——把**同一 run 的 job/lease 行**用另一会话按住即可
  构造稳定的阻塞点（`blob/...` 见 `backend/tests/scheduler/test_retention_lock_order_2010.py`）。

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
- **保留清理那一项的更正与修复（#2022）**：首版是**静态可达性分析**，且前提写错（保留期
  「数十天」）；更正后判定反转并已修。修复的验证为实跑：新回归
  `backend/tests/scheduler/test_retention_lock_order_2010.py` 在修复版下 **2 passed（0.96s）**、
  换回 `origin/main` 实现后**两条都以预期消息失败**（判据是第三会话 `FOR UPDATE NOWAIT`
  探测 + 「`pg_locks` 存在未获授锁」的就绪判定）；既有
  `backend/tests/scheduler/test_retention_cleanup.py` **16 passed**（证明删除语义未变）。

## Revisit

- **每轮增量核对**：任何**新增 / 修改**对 `job_instance` / `device_leases` / `plan_run` /
  `plan_run_host` 的写语句，先在本表定位该行，再核对该事务内**所有**相关行的加锁顺序是否
  满足 I1–I4；不必重新枚举全表。
- **回归护栏（`#1999` 已补合入门禁；措辞更正）**：四条锁序回归都是 PostgreSQL-only，
  需要真实 PG 行锁；但**「默认配置下会 skip，所以本地全绿是假绿」这个说法是错的**
  （`#2022` 实测更正：`conftest` 在导入测试模块前已把 `DATABASE_URL` 覆盖为
  testcontainers / CI 的 PG 库，那条 `startswith("sqlite")` 的 skip 分支在本仓 harness 里
  **不可达**；无 docker 时是在 conftest 阶段就报错，也不是 skip）。合入门禁已由 `#1999`
  补上：它们随 `pr-migrate-empty-db`（PR 阶段唯一有 PG service 的 required check）执行，
  接线由 `tests/test_lock_order_pr_path_contract.py` 做发现式守卫。
- **观测入口**：`stability_db_deadlock_total{engine}` 与告警 `StabilityDbDeadlockDetected`
  （`#1958`）。该计数器应长期为 0；出现增量即回到本表按行定位，而不是先怀疑语句形状。
- **保留清理（`#2022` 已修顺序，**持锁时长未改**）**：顺序已对齐 I1/I2。要把持锁窗口压到
  毫秒级还需把 `purge_run_storage_dirs` 移出事务——那会动 `#1521`/`#1698`「先文件后行」的
  自愈语义，属独立裁决。届时**不要**只把 deletes 挪到锁之前：候选选择依赖「锁内复核」
  与热路径互斥。
- **`released_leases`（`#2089` 已完成）**：该字段已从后端与前端类型两侧删除，不再作为
  契约的一部分；「租约是否释放」请以 reconciler / recycler 的路径与
  `stability_db_deadlock_total` 等观测为准，不要从 abort 的返回体推断。
- **表的位置**：若下一轮出现第二份同类表（或本表被别的文档大量引用），评估移入
  `docs/design/` 并同步 `docs/DOC-MAP.md`。

## 历史与关联

| 单 | 内容 |
|---|---|
| #992 | 发现 complete × extend-batch 半边（R06-F07），Revisit 预告了另一半 |
| #1959 | 回收器 Lease→Job 改为 Job→Lease + PG 并发回归 |
| #1980 | `coordinator_heartbeat`、`extend_job_lock` 两处反向 |
| #1985 | `abort_plan_run`（plan_run→job）改为 job→plan_run |
| #2022 | 保留清理改为 job→lease→plan_run；并更正本表「保留期数十天」的前提错误 |
| #2089 | 删除 `released_leases` 死字段（后端返回体/审计/日志 + 前端 `types.ts` 两侧） |
| #1960 | 把「以共享行为单位枚举」写进审查总纲 §3 第 7 条 |
| #1958 | 死锁指标与告警（本表的观测入口） |
