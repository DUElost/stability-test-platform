# ADR-0029 P2.5：人工登记簿 + 型号精确映射

Status: implemented
Class: feature

## Decision

`/projects` 产品面改为：

1. Fleet 事实只聚合 `device.model` / `platform`。
2. 列表默认只返回 `source=USER` 的人工项目。P1 六个回填 key 标 `SEED`，不出现在工作台。
3. admin 可新建项目，勾选型号后 preview/apply 写入 `match_models` 并归入设备。
4. SEED / LEGACY / NULL 不算映射冲突；其他 USER 项目冲突默认跳过，可 `reassign_conflicts`。
5. 不按前缀推断未知型号（ADR-0029 M-c）。D5 仍挂起。

## Alternatives

| 备选 | 放弃理由 |
|------|----------|
| 继续展示 HONOR-MLD 并标「非正式」 | 没有填写入口时仍是假项目目录，正是用户否定的形态 |
| 删除六个 SEED 行 | 设备/Plan/PlanRun 仍有 FK；先藏起来，归属由人工映射改写 |
| 前缀自动建项目 | 违反 M-c「不自动推断」 |
| 用 `device.project_id` 冒充已映射 | 把回填标签扮成业务项目 |

## Verification

- `python -m pytest backend/tests/api/test_project_routes.py -q`
- `npx vitest run src/pages/projects/`
- `npx tsc --noEmit`

## Revisit

- 是否归档/合并 SEED 行（FK 清空之后）
- 从规则里移除型号时是否同步解绑设备（当前 apply 只做加法）
