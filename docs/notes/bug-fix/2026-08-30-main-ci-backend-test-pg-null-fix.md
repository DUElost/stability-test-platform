# main CI backend-test 全量修复（PG + migration 场景）

Status: implemented
Class: bug-fix

main 全量 CI（workflow_dispatch + backstop 触发）backend-test 大面积失败：
`NotNullViolation: null value in column "project_id" of relation "plan"`。
CI 的 PG schema 由 `alembic upgrade head` 建（plan.project_id NOT NULL，
P1-B2 migration e6f7g8h9i0j1），大量测试内联 `Plan(...)` 构造不设归属直接
违反约束；本地 SQLite（create_all 宽松可空）掩盖了问题——「ORM 保持可空、
生产 migration 强制」的取舍在 CI 的 migration+create_all 混合建表下不成立。

## 修复

**1. conftest 双必填兜底（核心）**

- `_seed_plan_defaults`（autouse，每测试 TRUNCATE 后重建）：GENERIC 哨兵 +
  ops 专项必须预先存在——API 创建路径的 `_resolve_project_id` 在 INSERT
  Plan 之前查库，before_insert 钩子覆盖不到
- `_plan_default_attribution`（Plan before_insert 事件）：ORM 直接构造
  （`db.add(Plan(...))`）未显式设归属时落到 GENERIC/ops——语义与设计一致
  （P1-B2：「不显式归属 = GENERIC 显式不限」）

**2. 连带修复（全套 PG 场景暴露的既有失败）**

- **caplog 失效**（logging_setup #563 把 backend.* propagate 关闭）：
  conftest 在 import backend.main **之后**恢复 propagate——此前多个 WARNING
  断言测试在 PG 下静默失效（SQLite 下同样失效但未跑全）
- test_plan_barrier_timeout：8 处 POST/PUT payload 补双必填
- test_plan_dispatcher：3 个 P0-1「plan 无项目推断」测试改 GENERIC 语义
  （P1-B2 后 plan 恒有归属，推断分支不可达；GENERIC 是普通显式归属）
- test_project_routes：3 个列表断言含 GENERIC 哨兵（恒在）
- test_runs：`plan_run_project_id=1` 硬编码撞 GENERIC 占位 id → 用新建
  项目真实 id
- test_admission_queue_step2：conftest autouse 默认注册 pump，测试显式
  reset 到「未注册」基线
- test_devices ordered：断言改为集合（接口按 last_seen DESC NULLS LAST，
  NULL 组内顺序 PG 不保证——测试名与实现本就名实不符）

## Alternatives

- **批量改测试 fixture 补 project_id**：几百处内联构造，脆弱且重复。
- **去掉 migration 的 NOT NULL**：放弃生产约束，P1-B2 语义倒退。
- **CI 不跑 alembic**：放弃「测试跑在 migration 建的真实 schema 上」的
  设计意图（conftest 注释明示）。

## Verification

- 本地复现 CI 场景：postgres:16 容器 → `alembic upgrade head`（完整链
  含 c4d5e6f7g8h9i0→f6g7h8i9j0k1）→ `pytest backend/tests/`：
  **1790 passed / 0 failed**（首轮 16 failed → 逐类修复）
- ruff 全过
- 修复前失败清单（16 个）与修复后对照全部转绿

## Revisit

P1-B2「ORM 保持可空 + API 必填 + migration NOT NULL」的取舍修正为：
**测试环境也必须面对 NOT NULL**（conftest 兜底），生产/CI/本地三态行为
一致。夜间 backstop 恢复通过后 issue #578（ci/backstop-failed）自动关闭。
