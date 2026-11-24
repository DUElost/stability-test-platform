# Agent Note: Track B 列表服务端分页、只读投影与 Facet 聚合契约草案（#4117 / #496 / #3325 / #83）

Status: implemented
Class: architecture

## Decision

- 新增 [`docs/design/2026-11-24-list-pagination-and-facets-contract.md`](../../design/2026-11-24-list-pagination-and-facets-contract.md)（Draft v0.1），并在 `docs/DOC-MAP.md` 登记入口指针与刷新「最后更新」日期。
- 将 `#496`（前端列表基础设施）、`#3325`（`GET /devices` 与 `GET /hosts` 读端点写库及 N+1 懒加载）和 `#83`（`GET /plan-runs/{id}/devices` 无分页与 Minimap 全集诉求冲突）收敛为一份向后兼容、分阶段（B1 只读化 → B2 端点扩展 → B3 前端切换）的前后端契约草案。
- 明确确立 **PlanRun Minimap 轻量全集投影 + 表格真分页 + 抽屉单机详情按需加载** 的双视图分离边界，既压降 1,000 设备响应体又完整保留 Owner 指定的 Minimap 设定。
- 严格遵守 ADR-0059（`docs/development/writing-conventions.md`），全文规则句仅使用「必须 / 不得 / 建议 / 可以」并显式写出执行者。

## Alternatives

- **在 `#496` 评论区口头约定而非独立契约文档**：三项议题横跨前端、后端 API 契约与 DB 只读不变量，且涉及多个实施单元与测试门槛，评论区难以作为权威引用源。
- **直接将 `GET /plan-runs/{id}/devices` 硬改为分页**：会破坏 `DeviceOverview` Minimap 需要全集方块阵的核心体验，故明确采用轻量全集投影 + 表格真分页双视图分离。

## Verification

- 按 `docs/development/writing-conventions.md` §5 逐项自查新写规则句：术语统一使用推荐名（`Agent host`、`控制面`、`PlanRun`、`退役`/`离线`），强度词仅使用「必须 / 不得 / 建议 / 可以」并写明执行者，未误用「需要」「不能」作规则词。
- 本地运行 `.venv/bin/python scripts/run_gates.py check:quick` 全绿（见 PR 验证记录）。

## Revisit

- 契约评审定稿转 `Accepted` 时，需最终拍板 §5.1 采用独立路径 `GET /plan-runs/{run_id}/devices/minimap` 还是 `?view=minimap` 单一路径，并据此拆出 B1/B2/B3 实现单。
