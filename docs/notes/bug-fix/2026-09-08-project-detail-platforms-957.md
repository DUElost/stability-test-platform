# R04-F14 落地：项目详情填充派生 platforms（#957）

Status: implemented
Class: bug-fix

## Decision

列表 `_fill_summary` 计算真实 `platforms`（`_platforms_map`，ADR-0029
P1-B 派生：项目映射型号设备平台去重、UNKNOWN 不展示）；详情 `get_project`
不赋值，落回 schema 默认 `[]`——同项目列表有平台而详情恒空。

修复：`get_project` 复用 `_platforms_map(db, [project.id])` 同一派生
构造（一行，与 `match_models`/`device_count` 并列）。

## Alternatives

- **ProjectDetailOut 移除 platforms 字段**——放弃：详情页后续消费方需要
  该信息（列表字段契约一致），字段保留、填充即可；
- **抽取详情/列表共用构造 helper**——放弃：两处各一行 `_platforms_map`
  调用已一致；helper 化留待更多派生字段出现时。

## Verification

- **反例实证**：回退 projects.py 保留测试 → 新用例失败（详情 platforms
  恒空 [] ≠ 列表）；修复版全绿；
- 新增用例（`test_project_routes.py::TestDetailPlatformsParity` +1）：同
  项目列表与详情 platforms 对拍一致（验收标准）；
- `test_project_routes.py` 全套 **70 passed**；
- `check:quick` 与 PR 门禁：见 PR 描述。

## Revisit

- 无设备/无映射项目两处都为空列表（既有无设备用例断言详情
  `platforms == []` 不受影响）——一致性在「都有派生时相等」上验证。
