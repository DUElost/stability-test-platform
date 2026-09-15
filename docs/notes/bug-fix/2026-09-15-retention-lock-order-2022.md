# 保留清理加锁顺序对齐（job → lease → plan_run）

Status: implemented
Class: bug-fix

## Decision

把 `cron_scheduler.run_retention_cleanup` 的取锁顺序从 **plan_run → 子树** 改为
**job → lease → plan_run → 删除**，与共享行加锁全序 I1/I2 一致：

```
run_retention_cleanup（#2022 后）
  _retention_candidate_ids(db, cutoff)        # 只读，不再 FOR UPDATE
  _retention_prelock_subtree(db, run_ids)     # 先锁 job 行（按 id 升序），再锁 lease 行
  _retention_lock_runs(db, run_ids, cutoff)   # 再锁 plan_run（SKIP LOCKED）+ 锁内复核终态/年龄
  _retention_safe_ids → purge_run_storage_dirs → 删除子表 → 删除 PlanRun
```

删除内容、删除顺序、候选过滤（终态 + 超保留期 + 未被链引用）与业务语义**一律不变**；
锁内复核用「终态 + 年龄」重验预读结果（候选来自无锁读，可能已被并发改动），
`SKIP LOCKED` 保留原先由候选查询承担的互斥语义。

### 为什么必须修：本表首版的判定前提是错的

`docs/notes/architecture/2026-09-14-shared-row-lock-table.md` 首版把这处反向登记为
「接受，不修」，理由是「要成环需该 run 的 job/租约行被热路径持有并**共存数十天**」。
那个前提不成立：

1. **保留期不是数十天，是 3 天**——`backend/core/settings/scheduler.py:62`
   `plan_run_retention_days: int = 3`，ADR-0038 亦载明「终态 Run 默认 3 天由
   `run_retention_cleanup` 连 Job/StepTrace/租约/产物一并清理」；
2. **持锁窗口不是毫秒级**——保留清理在**持有 plan_run 行锁期间**还要做 NFS 目录回收
   （`purge_run_storage_dirs`，`#1521`/`#1698` 的「先文件后行」），窗口是秒级；
3. **成环只需一个已知形态**——「终态 run 里的 job 被迟到 / 重复 `/complete` 命中」
   （`#743` 幽灵 `/complete` 家族）；`complete_job` 走 `Job → Lease → plan_run`，
   与保留清理的 `plan_run → …` 直接成环。

即重叠窗口 = **清理事务持锁时长 × 该 run 的 job 被热路径触碰的概率**，与「共存多久」无关。
判定随之反转：修。

### 顺带更正的一处措辞错误

原表与 `#1999` 的 Note 都写过「锁序用例在默认配置下会 skip（sqlite），本地全绿是假绿」。
**该断言不成立**（实测）：`backend/tests/conftest.py` 在导入测试模块**之前**就把
`DATABASE_URL` 覆盖为 testcontainers / CI 的 PG 库，`pytestmark` 里
`startswith("sqlite")` 的 skip 分支在本仓 harness 内**不可达**——以
`DATABASE_URL=sqlite:///…` 运行，用例照常真跑并通过；无 docker 时是在 conftest 阶段报错，
也不是 skip。两处文档已就地标注更正（保留原句并标注「`#2022` 事实更正」，沿用 ADR-0038
「v0.2 事实更正」的写法）。

## Alternatives

- **维持「接受，不修」**：否决。理由本身被证伪（见 Decision）；留着等于让一条已知成环路径
  继续以「已评估过」的名义存在。
- **只把 `plan_run_retention_days` 调大 / 设配置下限**：否决。那只是压低概率，不消除环；
  且会延长历史数据保留（与 ADR-0020 的清理目标冲突）。顺序问题要用顺序解决。
- **只预锁 job 行、不预锁 lease 行**：否决。这样只满足 I2，`device_leases` 的删除仍在
  plan_run 之后 → 与 `complete_job` 的 `lease → plan_run` 仍成环（环只需两条边之一反向）。
- **把 deletes 挪到锁之前**：否决。候选与安全集必须在锁内复核（见 Decision），先删后验会
  在并发下删掉刚被复用的 run 的租约。
- **把 `purge_run_storage_dirs` 移出事务以缩短持锁窗口**：本单不做。那会动
  `#1521`/`#1698`「先文件后行、失败下轮重试」的自愈语义，属独立裁决；本单只解决**顺序**，
  并在 Revisit 里保留该窗口的洞见。
- **不加回归、只改代码**：否决。这类不变量在静态审查里不可见（这正是 2026-09 那轮全面审查
  漏掉整个死锁家族的原因），必须由能真跑的护栏承担。

## Verification

- **新回归**（`backend/tests/scheduler/test_retention_lock_order_2010.py`）：
  `pytest … -q` → **2 passed（0.96s）**。
  两条用例分别用另一会话按住**同一 run 的 job 行**与**lease 行**，再断言此刻 `plan_run`
  行仍可被第三会话 `FOR UPDATE NOWAIT` 立即锁定（判据：清理的等待必须发生在子树行上）。
- **负向对照**：把 `cron_scheduler.py` 换回 `origin/main` 的实现后，两条用例**均失败**并以
  预期消息报出（`锁序仍是 plan_run → job/lease`），恢复修复版后复绿——
  即该回归对本缺陷有鉴别力（正反两个方向都验过）。
- **既有面未回归**：`backend/tests/scheduler/test_retention_cleanup.py` → **16 passed**
  （删除语义不变的直接证据；该文件覆盖链式引用保留集、全链删除等 #936 语义）。
- **agent 单测 mock（#2022 锁路径改 `db.execute`）**：
  `backend/agent/tests/test_cron_scheduler.py::TestRunRetentionCleanup` 原先把
  `db.execute` 一律 stub 成 `[]`，导致 `_retention_lock_runs` 清空批次、提前 return
  （`commit` 0 次、`JobArtifact`/`JobInstance` 从未被 `query`）。已改为按
  `stmt.selected_columns` 的表名分发：`plan_run` 返回候选 id 行，job/lease 仍空。
  本地：`pytest …::TestRunRetentionCleanup -q` → **4 passed**；整文件 **13 passed**。
- **门禁**：`ruff check`、`check_governance_surface.py --check`、`pytest tests/ -q` 结果见 PR。

### 写这条回归时踩的两个坑（留给后续锁序测试）

锁序测试的**就绪判定**（「对方已经阻塞了吗」）比断言本身更容易出错，本单实测踩了两次：

1. **不能用 `pg_locks.relation` 关联 `pg_class`**：等**一行**（`FOR UPDATE` 撞上他人持有）
   在 PG 里表现为等待对方的 ``transactionid``，该 `pg_locks` 行的 `relation` 为 **NULL**，
   关联后永远匹配不到 → 20s 假红；
2. **不能读 `pg_stat_activity.query` 的文本按表名匹配**：阻塞会话显示的可能是**上一条**
   语句（实测：等 `transactionid ShareLock` 时 `query` 仍是上一条 `plan_run` 查询）→ 同样
   假红。

最终判据取「`pg_locks` 里存在 `granted = false` 且不属于本会话的锁」，够了且稳（0.96s 级）。
诊断这两次假红时，把活动会话与未获授锁按行写进文件（而不是 `print`）才看得到真相。

## Revisit

- **保留清理的持锁时长（未收口）**：NFS 目录回收仍在事务内、在 plan_run 行锁之后。若要把
  持锁窗口压到毫秒级，需把 `purge_run_storage_dirs` 移出事务——那会动
  `#1521`/`#1698` 的自愈语义，须独立裁决；届时**不要**只调换 deletes 与锁的先后。
- **skip 分支的措辞**：四条锁序用例仍保留 `startswith("sqlite")` 的 `pytestmark`。经本次实测
  它在本仓 harness 里不可达（conftest 会覆盖 `DATABASE_URL`）；保留它作为防御性写法可以，
  但**不要再把它当作「本地会 skip」的依据**。若后续要收口，应一次性改掉四处并同步本表与
  各 Note 的措辞。
- **`released_leases` 恒为 0（已由 `#2089` 删除）**：该「文档宣称 > 实现」残留已清理——
  后端返回体 / 审计 details / 日志与前端 `types.ts` 两侧同步删除，并按 `#787` 的
  「后端为权威、前端类型跟随」保持一致。
- **增量核对**：本表新增/修改任何写 `job_instance` / `device_leases` / `plan_run` /
  `plan_run_host` 的语句时，按共享行加锁表的 I1–I4 复核顺序，不必重新枚举。
