# Agent Note: ADR-0067 U1 — 项目级日志预设数据模型与写面（#4182 Phase 3）

Status: implemented
Class: feature
Issue: #4182
Date: 2026-11-28
ADR: ADR-0067（Accepted v1.0，D1 / §5 U1）

## Decision

按 ADR-0067 §5 落地第一交付单元（U1：数据模型与项目预设写面）：

1. **加性 Schema 迁移**（`backend/alembic/versions/a67u1c2d3e4f5_project_log_profile_schema.py`，`down_revision = b13u1c2d3e4f5`）：为 `test_project` 新增 `log_profile`（`JSONB NULL`）与 `log_profile_version`（`INTEGER NOT NULL DEFAULT 0`，附 `CHECK (log_profile_version >= 0)`），`downgrade()` 完整可逆，不回填存量行。
2. **单一白名单校验与规范化模块**（`backend/api/schemas/project_log_profile.py`）：
   - `ProjectLogProfile`（`ConfigDict(extra="forbid")`）仅允许 7 个已实证 `log_prepare` 键：`mobilelog_size_mb`（`256..20480`）、`mtk_auto_start`、`mtk_sublog_cmd`、`aee_mode`、`ylog_size_mb`（`64..10240`）、`ylog_rotate`、`ylog_required_streams`（必含 `{"android", "kernel"}` 且限实证白名单 `{"android", "kernel", "uboot", "lastlog"}`）。
   - `normalize_log_profile` 将全空/`None`/全 `null` 统一归一为 `None` 并去重归一化子流列表，确保无实质变更时不误推进 `log_profile_version`；`@model_serializer(mode="wrap")` 输出稀疏 JSON。
   - 模块置于 `backend/api/schemas/` 层，符合 `.importlinter` C1 分层（`services > api.schemas`）与 C2（`services` 不依赖 `api.routes`/HTTP），同时供 `backend/api/schemas/project.py`、写路径服务与后续 U2 合并/指纹复用。
3. **写路径与锁序配对**（`backend/services/project_registry.py`）：
   - 复用 `PUT /api/v1/projects/{project_key}`（不新增冗余端点，管理员可写、登录用户可读），`update_project_facets` 在 PostgreSQL 上对 `test_project` 取 `FOR UPDATE` 行锁（与 U2 `prepare_plan_run` 的 `Plan(FOR SHARE) → TestProject(FOR SHARE)` 无环锁序配对）；
   - `log_profile` 实质变化时同事务将 `log_profile_version` 原子加 1、记录含 `old`/`new`/`old_version`/`new_version` 的 `record_audit`，并在提交后广播 `emit_project_changed(project.id, "updated")`；`SEED`（422）与 `ARCHIVED`（409）保持既有拒绝守卫。
4. **前端类型同步**（`frontend/src/utils/api/types.ts`）：新增 `ProjectLogProfile`，在 `Project` 与 `ProjectUpdateInput` 补齐 `log_profile` 与 `log_profile_version`。

## Alternatives

- **独立新增 `PUT /api/v1/projects/{key}/log-profile` 端点**：违背 `add-api-endpoint`「能扩展现有端点就不新增」原则，也会迫使前端拆两次请求更新项目详情；复用 `PUT /api/v1/projects/{key}` 与既有 `UPDATABLE_FIELDS` / 审计 / 权限守卫完全同构且不增加 `projects.py` 行数。
- **将 `ProjectLogProfile` 放在 `backend/services/`**：会触发 `.importlinter` C1 分层违规（`backend.api.schemas` 不得反向导入 `backend.services`）；置于 `backend/api/schemas/project_log_profile.py` 既保持单向分层，又能被 `backend/services/` 与 `backend/api/schemas/project.py` 共用。

## Verification

- `TESTING=1 JWT_SECRET_KEY=test-secret ./scripts/project_python.sh scripts/run_pytest.py backend/tests/services/test_project_log_profile_u1_4182.py backend/tests/api/test_project_log_profile_api_u1_4182.py backend/tests/services/test_project_registry.py backend/tests/services/test_project_catalog.py backend/tests/api/test_project_routes.py -q`：164 passed
- `npm --prefix frontend run type-check`：通过
- `./scripts/project_python.sh scripts/run_gates.py check:quick`：16 gates 全绿（含 `layering` C1–C5、`gov-surface` S1–S15、`orphan-models`）

## Revisit

- 本单元（U1）只落地数据模型与项目预设写面，派发期五层合并、确认指纹与快照冻结归 U2，模板升级与前端编辑/展示归 U3（ADR-0067 §5）；
- 若未来经真机验证需扩展展锐 YLog 白名单子流，按 ADR-0067 D1.3 更新 `UNISOC_VERIFIED_YLOG_STREAMS` 即可，不得移除基础流 `android`/`kernel`。
