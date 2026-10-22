# U8 `unverified_probe_result` 审计守卫合规（#3752 / PR #3767）

Status: implemented
Class: bug-fix

## Decision

1. 将 `unverified_probe_result` 登记进 `BUSINESS_ACTIONS_ALLOWLIST`（90d），与
   `device_return_override` 同族——诊断证据，非账号/凭据安全事件。
2. `audit_unverified_probe*` 读侧去重改为
   `AuditLog.resource_type.in_(expand_resource_type_filter(...))`（#2872）；写侧
   `resource_type=` 按入口分支写成规范字面量 `device` / `job_instance`（#2778 / #2879），
   与 `resource_id` 对齐。
3. 该 action 列入 `_GENERIC_ACTIONS`：discharge/init 锚 device、complete/late 锚
   `job_instance` 是跨实体诊断，不是同一实体的词表分裂。

## Alternatives

- **统一只写 `device`**：否决——complete/late 的 `resource_id` 是 job id，写
  `device` 违反「resource_type := 实体表名」。
- **拆成两个 action**：否决——U8 验收与现有用例统一按
  `action="unverified_probe_result"` 断言。

## Verification

- `pytest --noconftest` 三份守卫文件全绿（含
  `test_non_generic_actions_use_a_single_resource_type`）。

## Revisit

若 U8 后续改为「一律锚 device、job id 只进 details」，再从 `_GENERIC_ACTIONS`
移出并收口为单一字面量。
