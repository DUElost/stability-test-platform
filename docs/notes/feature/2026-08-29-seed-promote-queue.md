# SEED 标签就地转正（promote 端点 + 待转正队列）

Status: implemented
Class: feature

ADR-0029 项目域 P0 收尾项：把 SEED 六行（HONOR-MLD / HONOR-ELA / ZTE-Z258 /
ODM-DAM / TRANSSION-X110 / LEGACY）从「看不见的第三类」变成有终点的待办
队列——消除「设备行显示归属它、筛选下拉里却没有它」的半隐身状态。

## Decision

**1. `POST /projects/seed/{key}/promote`（admin，就地转换）**

- 校验：行存在且 `source=SEED`（否则 404）；`LEGACY` 拒绝（422，兜底标签
  不是待转正对象）；`status=ARCHIVED` 拒绝（409）。
- 动作：`source SEED → USER`、`match_models` 预填其持有设备的型号（去重
  排序）、设备归属**不动**（`project_id` 不变——行身份即归属身份）、audit
  `promote_seed_project`、`emit_project_changed`。
- 幂等：转正后行已非 SEED，重复调用 404。

**关键约束发现：`project_key` 全局唯一（`uq_test_project_key` 不分 source）**
——SEED 行已占用 key，无法「新建同 key USER 项目」。最初按方案「创建新项目
+ 设备改归 + SEED 行归档」实现，被唯一约束直接打回（测试现场 IntegrityError）。
就地转换是唯一不违反约束的路径，且更简单：设备不用动。

**2. 修 list/inventory 的「按 key 集合剔除」残留**

`list_projects` 的 user 分支原本 `~key.in_(SEED_PROJECT_KEYS)` 二次剔除——
转正行 key 不变，即使 `source=USER` 仍被挡，这正是半隐身的**根因**（SEED
行被 source 过滤挡住之外的第二道）。改为只按 source 过滤；`_aggregate_inventory`
的 `mapped_project_keys` 与 `_rule_models_by_model` 同步去掉 key 集合判断。
`create_project` 的 reserved key 校验保留（防再造 SEED key 项目）。

**3. 前端「待转正（SEED 回填）」区块**

`listSeed`（`GET /projects?source=seed`）+ `promoteSeed` + 每行「转为项目」
按钮（window.confirm 确认，提示设备归属不变）；LEGACY 行显示「兜底标签」
无按钮。转正后 invalidate list / seed / inventory 三组 key。

## Alternatives

- **新建同 key USER 项目**：违反 `uq_test_project_key`，不可行（实测）。
- **SEED key 派生新 key**（如 HONOR-ELA-USER）：制造无意义的新名字，且
  「设备行显示归属 HONOR-ELA、下拉里 HONOR-ELA-USER」口径更乱。就地转换
  保持 key 即身份。
- **promote 前先弹窗选 key/名称**：SEED 的 display_name 已是人工可读名
  （荣耀 ELA），转正是「标签转正」不是「重新登记」，无需多一步。

## Verification

- 后端：`TestPromoteSeedProject` 6 测试（转正全链路 / LEGACY 422 / ARCHIVED
  409 / 重复 404 / 未知 404 / 非 admin 403）；45 passed；ruff 全过。
- 前端：ProjectsPage 2 新测试（队列渲染 + confirm 确认转正 / 取消不调用）；
  27 passed；全量 vitest + tsc + eslint 全过。
- 生产预期：5 个非 LEGACY SEED 可逐个转正；转正后卡片/筛选下拉立即可见。

## Revisit

P1 规则表落地后，「转正」预填的 match_models 应写进 `project_device_rule`
行；届时 SEED 队列可考虑自动提示（有设备且未转正的 SEED 出现在首页待办）。
