# #2047 退役主机日志尾读的审计从不落库（ADR-0038 D5 的补偿控制失效）

Status: implemented
Class: bug-fix

## Decision

ADR-0038 D-5 允许对退役主机做日志尾读，其**唯一**补偿控制是「必须审计」。但
`_audit_retired_log_tail()` 调用的 `record_audit()` 只做 `begin_nested()` + `flush()`
（`backend/core/audit.py:62-65`），而本端点的 dependency `get_db()` 在请求结束只有
`close()`（`backend/core/database.py:190-196`）——未提交事务随之回滚，
`host_retired_log_tail` 这行审计**从不落库**；同一次请求里唯一的 `db.commit()` 位于
`if migrated:`（SSH 凭据迁移分支）之内，退役机尾读通常走不到。

决定：在 `_audit_retired_log_tail()` 内部、`record_audit()` 成功之后立即 `db.commit()`。
调用点在 `query_agent_logs()` 的**任何业务写入之前**（`host = db.get(...)` 是只读，
审计写在 SSH 之前），因此这次 commit 只提交本次审计行，不会顺带提交无关状态；
失败时吞掉异常并 `rollback()`（既有语义不变：审计**非** fail-closed，写不进去不阻断
只读取证），只回滚是为了不把会话留在失败事务里影响后续取号路径。

## Alternatives

- **在 handler 的每个 return 分支前 commit**：本 handler 有 9 条返回路径（成功、
  凭据缺失、文件不存在、四类 SSH 异常、兜底），任一路径漏改就复现原缺陷；且审计
  语义是「已尝试读取也留痕」，与返回路径无关。否决。
- **改 `get_db()` 为结束即 commit**：影响全部 legacy 路由的事务语义，爆炸半径远超
  本缺陷。否决。
- **把审计改成 strict/fail-closed**：写路径（退役/解除退役）才需要 fail-closed；
  只读取证被审计故障阻断，收益小于代价。维持现语义。

## Verification

- `python -m pytest backend/tests/api/test_agent_log_query.py -q` → **5 passed**；
- **反事实验证**（本用例确实拦得住该缺陷）：把 `logs.py` 还原为改动前（`git checkout --`
  后重跑）→ `test_retired_host_log_tail_is_allowed_and_audited` **FAILED**
  （`1 failed, 4 passed`），恢复改动后复绿；
- 用例改为**独立连接**断言持久化（`engine.connect()` + 裸 SELECT `audit_logs`）：
  原断言用同一个 `db_session`（conftest 的 client 把 `get_db` 覆盖成同一 session），
  flush 出来的行对它可见，**无法区分「flush 过」与「已提交」**——这是本缺陷此前
  被测试判绿的原因。

## Revisit

- 审计「已尝试读取」与「读取成功」目前只有 details 里的字段区分，没有结果字段
  （`lines_read`/`error`）。若要按结果统计取证行为，需扩展审计 payload——属新需求，
  本单不做。
