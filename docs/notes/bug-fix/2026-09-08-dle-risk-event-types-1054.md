# DLE 风险汇总认具体 event_type（#1054）

## Decision

扩展 `log_observation._rows_from_device_log_events` 的 DLE 过滤条件：

1. 允许集 = 既有家族类型（`AEE`/`VENDOR_AEE`/`ANR`/`CRASH`）∪
   `resolve_device_log_event_type` 产出的具体类型（`JE`/`KE`/`NE`/`SWT` 等）。
2. 对仍写占位 `event_type`（`UNKNOWN`/`AEE`/`CRASH` 等）但 `event_subtype`
   已带具体类型的遗留行，用「占位 type + 具体 subtype」第二分支纳入。
3. `aee_entries` 改为「非 ANR 事件计数」，与 `by_type` 子类型键一致。

## Alternatives

- **仅扩 `_DLE_RISK_EVENT_TYPES` 元组**：不覆盖 `UNKNOWN`+`subtype` 遗留行，弃用。
- **SQL 侧完全排除占位符**：需维护非风险类型黑名单，且与 Agent 元数据双副本，弃用。
- **风险查询回退只读 signal**：违背 ADR-0028 DLE 权威，弃用。

## Verification

```bash
/home/debian13/stability-test-platform/.venv/bin/python -m pytest \
  backend/tests/services/test_log_observation.py -q
# 9 passed
```

新增用例覆盖：具体 `event_type=JE`、链接后 `KE` 不消失、`UNKNOWN`+`SWT` subtype。

## Revisit

- #783（`COUNT(DISTINCT path)` 低估）未在本单处理；若改去重口径需单独 Requirement。
- 具体类型列表与 `backend/agent/aee/metadata.py` 仍手工同步；若再漂移可考虑
  从 Agent 模块导出单一常量（需评估热更新边界）。
