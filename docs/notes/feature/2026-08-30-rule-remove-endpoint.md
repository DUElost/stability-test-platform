# 规则移除端点 + 改判告警（ADR-0029 生产复盘三件事）

Status: implemented
Class: feature

生产复盘（代码 × 生产库双向核验）发现规则表**只增不减**：P1 迁移把旧
match_models 的残留项（A57→MLD_LX2，8/20 人工纠正时未清）原样搬进规则表
且 is_active=true——map/apply 对已归属别的项目的型号 409，无 DELETE 端点，
错误规则无法通过平台修正。本次补上三件事。

## Decision

**1. `DELETE /projects/{key}/rules/{model}`（admin，记审计）**

- 删项目的一条活跃型号规则（物理删行）；`remove_project_device_rule`
  审计 + `emit_project_changed(rule_removed)`
- 已归属设备**不动**——心跳按新规则状态自然收敛（删除语义 = 撤回声明，
  不是批量搬设备）
- 404 语义：无活跃规则 → 404；非 admin → 403

**2. 详情页 [移除] 入口**

- 归属规则块改显**规则声明**（project.match_models 派生列表）+ 每型号
  移除按钮（admin，confirm 提示「设备归属不动」）；设备覆盖（modelsQ）
  保留为辅助行
- 移除后 invalidate detail / inventoryModels / inventorySummary

**3. `apply_attribution` 改判告警**

- 已归属设备被规则改判（model 变更 / 历史错误归属）→
  `logger.warning("attribution_reassigned serial=... model=... from=... to=...")`
- 未归属首次归属不告警（预期行为）；pinned 守卫不变
- 生产事故教训：一条错规则会静默搬走整批设备（A57→MLD_LX2 残留，若
  MLD_LX2 设备被置 NULL 或新接入即进 A57）——心跳无人值守路径无法
  「显式确认」，告警留痕是可行下限

## Alternatives

- **移除 = is_active=false（软删）**：物理删更符合「撤回声明」语义，
  审计已留痕；软删会让唯一索引的 ON CONFLICT 语义复杂化。
- **删除时批量重算受影响设备**：删除是声明变更不是数据修正——设备
  现状不动、心跳收敛；批量重算会把「第三态」（如 V552AA 的 228 台
  MLD_LX2）也搬走。

## Verification

- `test_project_routes.py::TestRemoveProjectRule` 3 个：删除 + 审计 /
  404 / 403；61 passed（该文件）+ ruff 全过
- `test_project_attribution.py::TestReassignWarning` 2 个：改判告警含
  serial / 首次归属不告警
- 前端：2 个移除测试（confirm 确认调用 / 取消不调用）；28 passed +
  tsc + 全量 vitest + eslint 全绿

## Revisit

生产数据修正（删 A57→MLD_LX2、补 V552AA→MLD_LX2）在端点部署后经 API
执行（写操作不走直改库）；「第三态」（V552AA 228 台无规则支撑）随补规则
自然消除。
