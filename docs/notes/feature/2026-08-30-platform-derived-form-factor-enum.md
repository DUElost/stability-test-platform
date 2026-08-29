# platform 删列改派生 + form_factor 收敛 enum（ADR-0029 P1-B1）

Status: implemented
Class: feature

P1-B 第一段：facet 域收敛——`test_project.platform` 删列（事实层在设备，
与 customer 不正交的矛盾随之消失），改为设备派生 `platforms`；`form_factor`
收敛为 CHECK enum（生产 '手机' / 'PHONE' 两套词表合一）。

## Decision

**1. Migration `d5e6f7g8h9i0`**（down_revision = c4d5e6f7g8h9，链连续）

- `UPDATE test_project SET form_factor='PHONE' WHERE form_factor='手机'`；
  其余非枚举值 → 'OTHER'；加 CHECK（PG ALTER；ORM 同步约束供 SQLite 测试）
- `DROP COLUMN test_project.platform`——A57「标 MTK 实际全 UNISOC」的矛盾
  随删列自动消失

**2. 派生 `platforms`（routes/projects.py）**

`_platforms_map(db, project_ids)`：一次聚合 `distinct(device.platform)`，
**UNKNOWN（探测失败哨兵）不展示**；列表 N+1 规避。list / _fill_summary /
get_project 统一填充。

**3. Schema/前端**

- `ProjectOut.platforms: List[str]` 取代 `platform`；Create/UpdateIn 删
  platform、`form_factor` 收敛 `Literal[PHONE/TABLET/WATCH/OTHER]`
- 前端 `Project.platforms: string[]`；facet 筛选语义改为「数组包含」
  （`facetMatches`）；卡片/详情/抽屉 badge 数组 join
- 创建/编辑对话框：platform 输入删除、form_factor 改为下拉（手机/平板/
  手表/其他 + 未设置）；输入类型放宽 `string | null`（后端 Literal 422 兜底）

## Alternatives

- **platform 保留列 + 校验与设备一致**：无法解决「列值说谎」——A57 标 MTK
  就是人在表单里填的，校验只能挡格式挡不了事实。删列让矛盾无法存在。
- **form_factor 不加 CHECK 只靠前端下拉**：SEED 脚本/P1 迁移可能再灌脏值；
  DB CHECK + ORM 约束 + Literal 三层，测试（create_all）与生产（migration）
  行为一致。

## Verification

- `test_platforms_derived_from_devices`：distinct + UNKNOWN 排除 + 无
  platform 键
- facet update 测试改断言「platform 不在响应、platforms 派生」
- fixture 删 platform 参数（项目级）；设备级 platform 不受影响
- 前端：ProjectsPage 3 测试更新（mock platforms 数组、facet testid
  `facet-platforms-*`、create payload 无 platform）；108 passed + ruff +
  tsc/vitest/eslint 全绿

## Revisit

P1-B2（下一段）：Plan project+specialty 双必填 +「通用」哨兵 + specialty
`ops` 词条 + 存量 9 行回填（migration 数据迁移）。
