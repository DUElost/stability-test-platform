# signal↔DLE 补链移出读路径（#556）

- **日期**：2026-08-29
- **关联**：Issue [#556](https://github.com/DUElost/stability-test-platform/issues/556)；服务 #528；Epic #527 / #519

## 决定了什么

补链（DLE 先落、signal 后补 `device_log_event_id`）从 `GET /plan-runs/{id}/watcher-summary`
的请求路径移到周期 sweep：

- 新增 `backend/scheduler/signal_link_reconciler.py`：`hold_scheduler_leadership`
  守卫（ADR-0027 P3-1）→ 限量查候选 `job_id`（`ORDER BY s.job_id DESC LIMIT`，
  每 tick 有界）→ `link_signals_to_device_log_events_sync` → **显式 `db.commit()`**。
- 在 `app_scheduler.py` 注册 `signal_link_reconcile`（`IntervalTrigger`），
  周期 `STP_SIGNAL_LINK_RECONCILE_INTERVAL_SECONDS`（默认 300），批次
  `STP_SIGNAL_LINK_RECONCILE_BATCH`（默认 200）。
- `watcher-summary` 改为纯只读：只留 `aggregate_signal_link_stats`。

理由（原实现的三重后果）：

1. `get_db()`（`core/database.py`）从不 `commit()`，UPDATE 随 `close()` 回滚 → **补链从未落库**。
2. 紧随其后的 `aggregate_signal_link_stats` 在**同一事务**内读，能看到未提交的 UPDATE
   → `archive.link_stats` 报的是「修好之后」的数字。实测反例：`linked_signals=1`、
   `link_rate=1.0`，而新会话里该 signal 的 `device_log_event_id` 仍是 `NULL`。
3. 前端 `PlanRunDetailPage` 按 3s/10s/30s 轮询，每次请求一次
   `UPDATE ... WHERE job_id = ANY(...)` 并对这些 signal 行持锁 —— 白跑且放大写。

signal 上送路径（`agent_api`）本就同事务即时链接；sweep 只排干错序遗留的存量。

## 放弃的备选

- **A：在 watcher-summary 里显式 `db.commit()`**。拒绝：保留「GET 写库」语义；仍不解决
  `link_stats` 乐观值（同事务读）；轮询持锁与写放大照旧。
- **把 repair 塞进 `device_lease_reconciler`**。拒绝：它的职责是租约过期与 job 终态，
  与日志链接无关；混进来会让两个互不相关的失败域互相拖累，且它的 15s 周期对补链过密。
- **不加 leader 守卫**。拒绝：多实例部署时每 tick 会重复执行同一批 UPDATE。

## 如何验证

```bash
unset TEST_DATABASE_URL
JWT_SECRET_KEY=test-secret python -m pytest \
  backend/tests/scheduler/test_signal_link_reconciler.py \
  backend/tests/api/test_plan_run_log_events.py \
  backend/tests/services/test_log_observation.py -q
```

三条新断言都用**新 session** 复查，因为原缺陷对请求自身的事务不可见：

| 用例 | 断言 |
|------|------|
| `test_reconcile_links_backlog_signal` | sweep 后新会话里 `device_log_event_id` 已回填 |
| `test_reconcile_is_idempotent` | 第二个 tick `scanned=0 / linked=0` |
| `test_watcher_summary_no_longer_links_signals` | GET 后新会话里仍为 `NULL`，且 `linked_signals=0` |

**反例证明会红**：把 `link_signals_to_device_log_events_sync(db, job_ids)` 加回
`watcher-summary`，`test_watcher_summary_no_longer_links_signals` 立即失败于
`linked_signals == 0`（实际 `1`，`link_rate=1.0`）—— 即后果 2 的乐观值。

原 `test_watcher_summary_read_repair_links_signal_and_reports_stats` 断言的是旧契约，
已改为 `test_watcher_summary_reports_links_made_by_reconcile_sweep`：
sweep 前 `linked_signals=0` → sweep → `linked_signals=1`，验证两段拼起来通。

## 何时重议

- `link_stats.link_rate` 长期不达标 → 先查 sweep 是否真在跑（日志
  `signal_link_reconcile_done`），再查 `scanned` 是否长期顶在 batch 上限（积压排不动，
  需调 batch 或周期）。
- signal 上送路径已能覆盖全部错序场景、sweep 长期 `scanned=0` → 可考虑摘掉 sweep。
- 前端 watcher-summary 轮询改造（P 类列表基础设施 / #496）时重新评估 batch 与周期。
