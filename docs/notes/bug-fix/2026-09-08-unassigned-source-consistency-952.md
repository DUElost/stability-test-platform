# R04-F06 落地：devices ?unassigned 与 inventory 同口径（#952）

Status: implemented
Class: bug-fix

## Decision

项目 inventory 的严格未映射口径（ADR-0029 v2.5）只把 **USER** 项目的活跃
成员行算映射（`projects.py` `_inventory_summary` 与型号级归属函数）；设备
列表的 `?unassigned=true` 过滤却把**任何**活跃 `ProjectModel` 行当映射
（含 SEED 回填成员）。SEED 存在时：统计说「还有待归属设备」、待归属列表
按 SEED 算已映射而找不到——两处注释都声称互相一致，实现并不一致。

修复：`devices.py` 的 `unassigned` 分支 `mapped_models` 子查询加
`join(TestProject)` + `TestProject.source == _USER_SOURCE`——与 projects.py
同一派生条件。模块内补 `_USER_SOURCE` 常量（不跨模块 import 私有符号）。

## Alternatives

- **projects 统计放开到含 SEED**——放弃：SEED 是 P1 回填标签（工作台不
  展示、不可映射），「型号级归属函数无逐设备例外」注释与 v2.5 裁决都以
  USER-only 为权威口径；devices 端对齐成本一行；
- **两端提取共享派生函数**——放弃：跨路由模块共享私有查询面需新的公共
  模块（backend/services/...），两处条件已注释互指、单行对齐即可；若
  未来第三处消费出现再提取。

## Verification

- **反例实证**：回退 devices 改动保留测试 → 新用例失败（SEED 型号被
  排除在待归属外、明细 1 ≠ 统计 2——正是 issue 描述的打架）；修复版全绿；
- 新增用例（`test_devices.py::TestProjectAttribution` +1）：SEED-only 型号
  设备与无型号设备计入 `?unassigned=true`、USER 映射设备不计入；并与
  `/projects/inventory/summary` 的 `unassigned_devices` 对拍（=2）；
- `test_devices.py` + `test_project_routes.py` 全套 **90 passed**；
- `check:quick` 与 PR 门禁：见 PR 描述。

## Revisit

- `attribution_source` 派生（mapped/unmapped 两态，列表默认展示）同样基于
  ProjectModel 活跃行——SEED 行会让设备显示 mapped+SEED project_key？既有
  `_model_to_projects` 已按 source 过滤（项目归属投影），若设备页派生与
  inventory 仍存在残余不一致，属后续对拍范围（本单只收口 unassigned
  过滤端点）。
