# ADR-0029 P2.5a：Fleet 事实层；映射必须人工填写

Status: implemented
Class: feature

## Decision

P2.5a 把 Fleet 事实从回填标签里拆出来，并明确「已映射项目」不能用 `HONOR-MLD` 冒充。该切片已被 P2.5 用户登记簿取代：工作台不再展示 SEED 标签，映射走 `match_models` 写入口。

## Alternatives

见后续 note。

## Verification

当时：`backend/tests/api/test_project_routes.py` + `src/pages/projects/` vitest。

## Revisit

已被 [2026-08-20-project-registry-user-mapping.md](./2026-08-20-project-registry-user-mapping.md) 取代。
