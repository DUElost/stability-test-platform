# match_models 列 drop（ADR-0029 P1 收尾）

Status: implemented
Class: simplification

规则层迁移终点：`test_project.match_models`（JSONB 数组）列删除。P1-A 起
写侧已停（map/apply、promote 写 `project_device_rule`）、读侧已派生
（`_rule_values_for_project`），本 commit 纯删列 + 清理最后一处 ORM 直读。

## Decision

**1. Migration `f6g7h8i9j0k1`**：drop 列（downgrade 用 JSONB server_default
还原——列已无写入方，还原为空数组语义一致）。

**2. `devices.py` 的 rule 判定切规则表**：`_attribution_source` 原读
`project.match_models`（ORM 列）判定「rule」，改为 `resolve_project_id(db,
model) == project.id`（活跃规则精确匹配，与心跳同一解析入口）。函数签名
加 `db`，三个调用点（list / get / bulk）同步。

**3. 清理**：create_project 构造删 `match_models=[]`；TestProject ORM 删列
（连带删 JSONB import）；测试 fixture 的构造参数批量删除；test_devices 的
rule 判定 seed 改用 `ProjectDeviceRule` 行（语义与生产一致）。

**4. API 契约不变**：`ProjectOut.match_models` 字段保留，由
`_rule_values_for_project` 派生填充——前端无需改动。

## Verification

- 162 passed（project_routes / devices / heartbeat / attribution / plans）+
  ruff 全过
- rule 判定路径有测试覆盖：`test_devices.py::TestProjectAttribution`（rule
  判定走规则表）+ `test_project_attribution.py`（resolve 层）

## Revisit

无——P1 规则表迁移闭环。夜间 sweep 维持「不做」决策（心跳覆盖全部写入
路径，页面规则表实时暴露漂移）。
