# ADR-0029 P2.5a：Fleet 事实层；映射必须人工填写

Status: implemented
Class: feature

## Decision

`/projects` 改为编组工作台的只读切片：

1. **Fleet 事实**只聚合 `device.model` / `platform`。
2. **已映射项目**只表示人工填写的映射。P2.5a 无规则表，该列恒为「待手动填写」。
3. **`HONOR-MLD` / `ZTE-Z258` 是 P1 脚本回填标签**，单独列「回填标签（非正式）」。它们不能代表客户、项目或机型，也不再填进「已映射项目」。
4. API 分列 `backfill_project_keys` 与 `mapped_project_keys`（后者 P2.5a 恒 `[]`），避免把 `device.project_id` 叫做映射。
5. 下方卡片标明「非正式回填」；详情页写清这是回填标签，型号反查叫「当前挂在此标签下的型号」。

## Alternatives

| 备选 | 放弃理由 |
|------|----------|
| 用 `device.project_id` 填「已映射项目」 | 把 HONOR-MLD 扮成业务项目，正是用户要澄清的误导 |
| 完全隐藏回填标签 | 设备当前仍挂在这些 key 上，运维需要看见；藏起来会丢归属线索 |
| P2.5a 就建规则表 | 先把口径改对；人工编辑留给 P2.5b |

## Verification

- `python -m pytest backend/tests/api/test_project_routes.py -q`
- `npx vitest run src/pages/projects/`
- `npx tsc --noEmit`

## Revisit

- P2.5b：规则 CRUD 后 `mapped_project_keys` 才有值
- P2.5c：真正的项目 CRUD 取代回填标签作为编组入口
