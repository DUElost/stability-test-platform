# UNIVIEW 事件纳入 PlanRun 风险汇总（#1075）

Status: implemented
Class: bug-fix

## Decision

`log_observation` 风险聚合仅认 `AEE`/`VENDOR_AEE`/`ANR` 家族，展锐 UNISOC
`UNIVIEW` 信号与 DLE（ADR-0032）被两侧过滤完全漏计。将 `UNIVIEW` 加入：

- `_DLE_RISK_FAMILY_EVENT_TYPES`（DLE 权威路径）
- `_SIGNAL_RISK_CATEGORIES`（未链接 signal 路径）
- `_LINK_RATE_CATEGORIES`（Unisoc reconciler 同样注册 DLE，#528 链接健康）

## Alternatives

- **watcher-summary 单独加 UNISOC 桶**：与 ADR-0028 统一风险入口目标冲突，弃用。
- **把 UNIVIEW 映射为 CRASH**：丢失 `event_subtype=event_name` 粒度，弃用。

## Verification

```bash
/home/debian13/stability-test-platform/.venv/bin/python -m pytest \
  backend/tests/services/test_log_observation.py -q
```

## Revisit

- dropbox 辅信号若扩展为独立 category，需另开 Requirement 评估是否并入风险集。
