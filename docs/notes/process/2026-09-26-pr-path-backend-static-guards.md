# PR 路径加跑后端静态守卫

Status: implemented
Class: process

关联：#3423（#3421 回归漏进 main 的实例）、#3017（保留分层守卫）、#1547（同库 DATABASE_URL 拒载）、
`tests/test_lock_order_pr_path_contract.py`（同模式先例）。

## Decision

- `.github/workflows/ci.yml` 的 `pr-migrate-empty-db`（已是 required check、带 PG service、已跑
  `alembic upgrade head`）末尾新增「Run backend static guards」步骤，以 `env -u DATABASE_URL`
  运行 `backend/tests/` 下文件名含 `guard` 的 9 个测试文件（约 100 例，本地 ~35s）。
- 新增 `tests/test_static_guard_pr_path_contract.py`：凡 `backend/tests/**/test_*guard*.py`
  都必须出现在 PR 路径某个 pytest 步骤的代码行里，且该步骤剥离同库 `DATABASE_URL`；
  新增同名守卫却不接线，`pr-agent-tests` 即红。
- 取舍来源：2026-09-26 PR 审计后 owner 在三选一中裁定「只跑静态守卫」——PR 仍不跑全量
  `backend/tests/`（夜间 `backend-test` 兜底不变）。

## Alternatives

- PR 改到 `backend/` 就跑全量 `backend-test`（非 required）：覆盖最全，但 Actions 分钟与排队时间
  明显上升，且非 required 不拦合入；未采纳。
- 保持现状只靠夜间兜底：回归最长可在 main 上停留一天；未采纳。
- 新建独立 job：不在分支保护里、不拦合入（与锁序回归接线时的理由相同）；未采纳。

## Verification

- `python -m pytest tests/test_static_guard_pr_path_contract.py`：用 main 版 ci.yml 时 2 failed，
  加步骤后 3 passed。
- 本地等价执行新步骤（隔离临时 PostgreSQL，非生产库）：100 passed。
- `python -m pytest tests/`（按 CI 的三个 `--ignore`）：1820 passed, 68 skipped。
- `python scripts/run_gates.py check:quick`：16 gates 全绿。

## Revisit

- 若 `pr-migrate-empty-db` 耗时逼近 15 分钟上限，或静态守卫增长到明显拖慢队列，改为按改动路径过滤。
- 若出现「文件名不含 guard 但同属静态断言」的漏网回归，给契约测试加登记表第二档。
