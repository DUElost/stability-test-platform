# plan_run_abort 批量终态化：ORM 同步契约显式化

Status: implemented
Class: bug-fix

## Decision

`abort_plan_run` 的 PENDING 批量终态化（#492）使用
`db.execute(update(JobInstance)...)` 完成状态迁移。该 UPDATE 之后，函数
用 `all_jobs` 的**内存**状态计算 `has_active_jobs`，据此决定是否走兜底
聚合 `apply_plan_run_aggregation(pr, all_jobs)`。

实测确认：SQLAlchemy 的 `session.execute(update(...))` 默认
`synchronize_session` 行为**已经**把新值写回 session 中的 ORM 对象，
因此当前代码路径不会出问题。风险在于这个依赖是**隐式**的——一旦有人
按批量场景的常规做法改成 `synchronize_session=False`（省一次 SELECT/
evaluate），内存中的 job 仍是 `PENDING`，`has_active_jobs` 恒为 True，
于是 `total_job_count == 0` 的 legacy run 既走不到计数器聚合
（`total > 0` 为假）也走不到兜底聚合，**PlanRun 会停在 RUNNING 永不收敛**。

因此本次不改逻辑，只把依赖显式化：UPDATE 上写明
`execution_options={"synchronize_session": "fetch"}` 并注释说明下游依赖，
另加一条回归测试锁定该契约。

涉及文件：

- `backend/services/plan_run_abort.py`（UPDATE 增加 `execution_options` + 注释）
- `backend/tests/api/test_plan_run_abort_api.py`（新增
  `test_abort_batch_update_keeps_orm_in_sync_for_convergence`）
- `deploy/control-plane/env/.env.backend.example`（补
  `AI_ASSISTANT_FERNET_KEY` 占位，与 `backend/.env.example` 对齐——此前
  部署样例缺该键，合入 ADR-0031 后易在部署时静默降级）

## Alternatives

- **在批量 UPDATE 之后手工回写 `pending_to_abort` 各对象的字段**
  （status/status_reason/execution_state/ended_at/updated_at）：曾按此方案
  实施，但对照实验证明它与框架默认行为完全重复（加与不加，`all_jobs`
  内存状态都是 `ABORTED`、run 都收敛为 FAILED），属冗余代码；且一旦未来
  改用 `synchronize_session=False`，手工回写会掩盖真实问题。已放弃。
- **`db.expire_all()` 后再判定**：会连带丢弃同一 session 中其他已加载
  对象的状态（含 `pr` 自身与 lease 相关对象），副作用面大于收益。
- **改用 `rowcount` / 重新 `select` 判定活跃作业**：多一轮查询，且把
  「读内存」改成「读库」会改变该函数既有的锁内一致性语义。

## Verification

- `python -m pytest backend/tests/api/test_plan_run_abort_api.py -q`
  → **17 passed**（含新增契约用例）。
- 对照实验（证明测试能捕获契约破坏）：把 `synchronize_session` 临时改为
  `False`，新增用例失败于
  `AssertionError: assert 'RUNNING' != 'RUNNING'`（run 卡在非终态）；
  同时 `mem statuses=['PENDING']`、`has_active_jobs=True` —— 与默认行为
  `['ABORTED'] / False / pr.status=FAILED` 形成对照。恢复为 `"fetch"` 后
  全绿。
- `ruff check backend/services/plan_run_abort.py` → All checks passed。

## Revisit

- 若后续为性能把该 UPDATE 改成 `synchronize_session=False`，必须同时改写
  `has_active_jobs` 的判定（例如按 `pending_ids` 差集排除，或重新查询），
  否则上述 legacy run 卡死会复现。
- 若 SQLAlchemy 升级改变了 `session.execute(update(...))` 的默认
  `synchronize_session` 语义，本 note 的实测结论需重做（复跑对照实验即可）。
