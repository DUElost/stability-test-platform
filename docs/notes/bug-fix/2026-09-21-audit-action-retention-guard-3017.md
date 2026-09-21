# 审计 action 分层保留登记守卫（#3017 / ADR-0049 D2）

Status: implemented
Class: bug-fix

## Decision

1. 在 `audit_log_cleanup.py` 增加 **`BUSINESS_ACTIONS_ALLOWLIST`**：裁剪仍用
   NOT IN（D2 不变）；allowlist **只**服务守卫——新 action 必须进
   SECURITY / SESSION / 本表之一，禁止默默进 90d。
2. AST 守卫 `test_audit_action_retention_guard_3017.py`：扫写侧 `record_audit*`；
   解析字面量与 `IfExp` 双常量；`SUMMARY_ACTION` 名；动态构造点进
   `_DYNAMIC_ACTION_SITES`（僵尸行亦红）。
3. 把现存动态闭合集（`ai_assistant_action_*`、`abort_*`、`scan`/`scan_rebaseline`、
   `patrol_stall_detected` / `job_running_timeout` 等）点名进 allowlist。

## Alternatives

- **business 也改成枚举裁剪**：否决——违背 ADR-0049 D2「对新 action 封闭、不延迟清理」。
- **只加注释提醒**：现状即此，无机械牙齿。

## Verification

- `python -m pytest backend/tests/test_audit_action_retention_guard_3017.py -q`
- `python scripts/run_gates.py check:quick`

## Revisit

新增动态 `action=f"…"` / 形参时：先把闭合全集写入 allowlist，再登记
`_DYNAMIC_ACTION_SITES`。

> 2026-09-21 修订：本条原写「行号漂移会使僵尸断言变红」——登记键当时含 lineno，
> 于是 #3031 在 `plan_run_abort.py` 上方插入 3 行就让 main 当场红，且报错指向错误的
> 处置。登记键已改为**形态键**（文件::函数链::`action=` 源码 → 次数），行号漂移不再
> 惊动守卫；见
> [`2026-09-21-audit-action-dynamic-key-anchor-3017.md`](2026-09-21-audit-action-dynamic-key-anchor-3017.md)。
