# 项目页 KPI 换待办 + 归属规则表主区块 + 归档态（ADR-0029 P0）

Status: implemented
Class: feature

ADR-0029 项目域 P0 第四步：让 /projects「页面不再说谎」——顶部 KPI 从虚荣
指标（人工项目/设备总数/在跑 Run）换成唯一需要人行动的数字「待归属」；
归属规则表从折叠次区块提为主区块并显示规则目标 vs 实际覆盖率；归档态可见
（修「归档是 no-op」）。SEED 转正队列单独下一轮（见 Revisit）。

## Decision

**后端**

1. `GET /projects` 新增 `status` 过滤（ACTIVE/ARCHIVED，缺省 = 全量，行为
   不变）。归档项目此前与活跃项目同列表返回（「归档是 no-op」），前端卡片
   现在按 status 显示「已归档」badge。
2. `InventorySummaryOut` 新增 `unassigned_devices`——**严格口径
   （`project_id IS NULL`）**，与 `GET /devices?unassigned=true` 数字一致。
   区别于 inventory 既有的「非 USER 项目」宽口径（SEED 归属如 LEGACY 在
   宽口径算 unassigned，严格口径不算）。页面 KPI 与设备页筛选因此同数。

**前端**

3. KPI 带 3 格改为「**待归属**（unassigned_devices，最大、>0 时 warning 色）
   / 人工项目 / 在跑 Run」。设备总数归 /devices 页，不再重复。
4. 归属规则表从可折叠 Card 提为主区块（标题「归属规则」，删除折叠交互与
   `inventoryOpen` state）：每行「规则目标 → 实际」——`{key} {covered}/
   {device_count} ✓/⚠`，`covered = device_count - unassigned_device_count`，
   缺口 ⚠ + tooltip「N 台未归属，需补规则或手动归入」。勾选映射交互保留
   （勾选型号 → 映射到项目，是规则写入面）。

## Alternatives

- **KPI 用 inventory 宽口径**（非 USER 即未归属）：数字会与设备页
  `?unassigned=true` 不一致（SEED 归属混入），两页对不上又是一次口径
  事故。严格口径成本一次 count 查询，值得。
- **SEED 转正本轮一并做**：需要新后端端点（promote：建 USER 项目 + 设备
  改归 + SEED 归档）+ 前端队列。与规则表主区块是两件事，拆开评审更快。
- **规则表行内直接编辑规则**：P1 规则表落地前，映射交互维持 map/preview +
  apply 两段式（现状已可勾选批量归入），本轮只补展示。

## Verification

- 后端：`test_project_routes.py` 新增 status 过滤测试（ACTIVE/ARCHIVED/缺省
  全量三态）；inventory summary 断言更新（空 fleet 含 unassigned_devices=0；
  严格口径 3 台 NULL vs SEED 归属不算）；40 passed；ruff 全过。
- 前端：tsc 通过；ProjectsPage/InventoryModelsTable 测试 25 passed；
  全量 vitest 无失败；eslint 全过。
- 生产口径对照（2026-08-29 实测）：`unassigned_devices` 将显示 117
  （= 设备页未归属筛选数），MLD_LX3 77 / Z2581 30 / Z2582 10 在规则表
  逐行标 ⚠。

## Revisit

P1 规则表（project_device_rule）落地后：规则的「目标」从 match_models
切到规则表，`covered` 判定同步换数据源；SEED 转正队列（promote 端点 +
「待转正」区块）是 P0 收尾项，单独 PR。
