Status: implemented
Class: architecture

# Track B 规模化分页与 Facets 契约定稿 v1.0（#4117）

## Decision

全面吸收独立架构与契约复核报告的全部 P0（4 项）、P1（3 域细化）与 Q1–Q7 裁决，将 `docs/design/2026-11-24-list-pagination-and-facets-contract.md` 由 `Draft v0.1` 修订定稿为 `Accepted v1.0`，并同步更新 `docs/DOC-MAP.md`：

1. **P0-1 / Q1（后台对账与读端纯只读原子接管）**：明确 B1 必须在同一发布单元内先扩展 `session_watchdog`（覆盖 `NULL`/过期 `ONLINE`/`DEGRADED`/存量 `OFFLINE` 关联设备级联，保留 Job/租约/ADB/退役事实）并统一 SQL 有效状态谓词，再移除 4 个库存 GET（含两个详情 GET）的同步写库。
2. **P0-2 / Q3（跨页批量选择门槛）**：`DevicesPage` 保留「当前页勾选」与「独立 `filter_query` 预览冻结 ID + 确认上限 + 重验权限/资格」两条显式路径；`HostsPage` 同样显式区分跨页安装确认；`PlanExecutePage` 显式排除出本契约并保留共享全量 helper。
3. **P0-3 / Q4（旧响应双形态冻结 + 独立 `/hosts/facets`）**：冻结 `/devices`、`/hosts`、`/plan-runs/{id}/devices` 旧路径返回形态与缺省值；采用独立 `GET /api/v1/hosts/facets`（10 键，补齐 `trackable_agent_count` 分母）。
4. **P0-4 / Q5 / Q6（PlanRun 独立 Minimap 与单 Job 详情）**：定稿独立端点 `GET /api/v1/plan-runs/{run_id}/devices/minimap`（7 个命名字段 + `facets` + host 显示名映射 + `stuck_job_ids`，1,000 Job 原始 UTF-8 JSON `≤ 384 KiB`）、分页新增 `filtered_total` 不覆盖 `total`、新增 `GET /api/v1/plan-runs/{run_id}/jobs/{job_id}`（校验归属并按实际浏览集合驱动上/下一台）。
5. **ADR-0059 合规与四类门禁**：逐句收紧为「执行者 + 条件 + 必须/不得/建议/可以 + 单一动作」，将规模压测（150 host × 3,750 设备、`projection=list&limit=50 ≤ 100 KiB`、p95 `< 200 ms`）与功能回归拆分为四类可验证门禁，明确 B1（1 PR）→ B2（3 切片 PR）→ B3（3 页面 PR）的实施路线。

## Alternatives

- **保持 v0.1 边做边改**：会直接踩中 `session_watchdog` 漏设备级联、破坏 `site_config`/部署 SOP 旧响应契约、以及 `DevicesPage` 丢失 ADR-0057 E3 跨页退役工作流等 P0 风险，因此必须先定稿 v1.0 再进实施。

## Verification

- `scripts/run_gates.py check:quick`（含 `gov-surface` S1–S15、S5x 校验）
- 对照 ADR-0059（`docs/development/writing-conventions.md` §2–§5）逐句自查全部规则句

## Revisit

- B2 压测阶段若隔离 PostgreSQL `EXPLAIN (ANALYZE, BUFFERS)` 证实特定筛选组合需要索引，将在独立索引 PR 中补充记录，不修改本契约主架构。
