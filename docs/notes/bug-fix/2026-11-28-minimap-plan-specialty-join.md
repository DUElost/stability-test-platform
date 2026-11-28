# Fix Minimap `Plan.specialty_key` AttributeError via `Specialty` Join

Status: implemented
Class: bug-fix
Related: #4221

## Decision

修复 `backend/services/plan_run_devices.py` 中 `build_plan_run_devices_minimap` 查询 `Plan.specialty_key` 导致的 `AttributeError`（生产环境 `GET /api/v1/plan-runs/{run_id}/devices/minimap` 返回 HTTP 500）：

- `Plan` ORM 模型（`backend/models/plan.py`）通过外键 `Plan.specialty_id` 关联字典表 `Specialty`（`backend/models/project.py`），专项键字段为 `Specialty.key`，`Plan` 表自身不存在 `specialty_key` 列。
- 将查询改为显式 `outerjoin(Plan, Plan.id == PlanRun.plan_id).outerjoin(Specialty, Specialty.id == Plan.specialty_id)` 并投影 `Specialty.key.label("specialty_key")`，保持与 `resolve_run_offline_policy(plan_snapshot, specialty_key)` 的调用签名完全一致。
- 在 DB-backed API 测试 `backend/tests/api/test_plan_run_b2_3_minimap_pagination_job_83.py` 补齐回归用例，覆盖 Sleep（长离线标红、短离线与 Host 离线不误报）、PowerCycle（经 `Plan.specialty_id -> Specialty.key` 豁免）与 Aging DDR（经 `plan_snapshot` 脚本名豁免）。

## Alternatives

- 仅从 `PlanRun.plan_snapshot` 读取 `specialty_key`、移除对 `Plan` 表的 join：拒绝。部分历史 PlanRun 的 `plan_snapshot` 可能为空或未冻结 `specialty_key`，经 `Plan.specialty_id -> Specialty.key` 回退兜底才能保证开关机专项始终可靠豁免。

## Verification

- 在隔离 testcontainers PostgreSQL（`unset TEST_DATABASE_URL`）上经内存硬顶 runner 运行 DB-backed API 与策略单元测试：
  - `backend/tests/api/test_plan_run_b2_3_minimap_pagination_job_83.py`
  - `backend/tests/services/test_plan_run_offline_policy.py`
  - `tests/test_api_response_shape_contract.py`
  （24 passed）
- 运行 `scripts/run_gates.py check:quick`。

## Revisit

- 若未来 `PlanRun` 在创建时强契约冻结 `plan_snapshot.plan.specialty_key` 且完成历史数据回填，可评估省略 Minimap 查询中的 `Plan`/`Specialty` 双 `outerjoin`；当前单次主键等值外连接成本可忽略。
