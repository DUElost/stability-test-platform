# 健康页趋势空态 + 设备日志盘展示精度

Status: implemented
Class: bug-fix

## Decision

PR #379 收口后 revisit 队列中两项前端缺口（#205 容量历史策略仍待切盘，未动）：

1. **趋势图空态**：`history` 四段序列全空时（分源未配 job fail-closed、Prometheus
   不可用等）展示 `InlineEmpty`，文案按告警/监控状态分支，不再静默空白坐标轴。
2. **设备日志盘 `usage_percent` 展示**：改 `toFixed(2)`（`formatUsagePercent`），
   与 Agent `round(..., 2)` / 控制面 API 一致；避免 `94.99` 显示为 `95.0%`
   的肉眼误读（色带仍与阈值同源，非告警分级 bug）。

## Alternatives

- 空态只写「暂无数据」：被否，分源 misconfig 需指向 `STP_STORAGE_NODE_JOB`。
- 展示改 `toFixed(1)` + 阈值也改一位小数：被否，后端与 Agent 均为 2 位。

## Verification

- `frontend/src/pages/storage/FileServerPage.test.tsx`：空历史 + `94.99%` 断言。
- vitest 该文件 + eslint。

## Revisit

- #205 切盘后容量历史是否仍走控制面挂载：见
  `2026-08-22-storage-health-audit-fixes.md` §Revisit。
