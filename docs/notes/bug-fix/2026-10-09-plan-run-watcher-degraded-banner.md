# PlanRun 异常仪表盘恢复「异常采集降级」提示

Status: implemented
Class: bug-fix

## Decision

`GET /plan-runs/{id}/watcher-summary` 一直返回 `watcher_capability`：本次运行各 Job 中「最降级」的一档，
由 `backend/services/plan_run_watcher_summary.py` 的 `_aggregate_watcher_capability` 计算。
取值 `unavailable` 表示 Watcher 探测全失败，AEE 补采（reconciler）也可能没有运行。
Watcher 默认策略是 DEGRADED，即能力不可用时 Job 照常跑（`backend/agent/watcher/policy.py`）。
所以这种运行的异常仪表盘会显示「0」，人会把它读成「没崩溃」。

降级提示原在 `WatcherSummaryCard`。该卡被 AnomalyDashboard 取代后，提示没有迁移；
2026-08-21 清理死组件（`a58e45e3`）时随卡一起删除，此后前端对 `watcher_capability` 零引用，
类型注释里描述的「降级徽章」已不存在。

本单在 `frontend/src/components/plan-run/AnomalyDashboard.tsx` 的统计卡上方恢复提示：

- 仅当 `watcher_capability === 'unavailable'` 时显示 `role="status"` 的告警条；
- 文案说清「对这些设备来说，『无异常』不等于没有崩溃」；
- 技术取值放在悬浮说明里；
- 加载中 / 加载失败时不显示（没有数据就没有结论）。

`polling`（inotifyd 不可用、由补采承担）与 `skipped`（按策略未启动）沿用原设计，不提示。

来源：UI 人类可达性审查（2026-10-09）B3；owner 同意。

## Alternatives

- **放在页头徽章 / 技术详情开关后面**（原 `WatcherSummaryCard` 的做法）：要先点开详情才看得到，
  而误读恰好发生在看数字的那一眼。改为紧贴统计数字的常显告警条。
- **按设备列出哪些设备降级**：后端只聚合了最降级的一档，没有逐设备计数；新增聚合属于后端改动，
  不在最小方案内。若需要，再在设备矩阵加 `watcher_capability` 列。

## Verification

- 先加测试，在旧实现上运行：`unavailable` 的正向断言失败；`polling` / `inotifyd_realtime` / `skipped` / 空值
  四条负向断言与「加载失败不提示」本来就通过（守的是不误报）。
- 改后 `npx vitest run src/components/plan-run/ src/pages/execution/` 共 278 条全绿。
- 目视：渲染 `unavailable` 且全 0 的仪表盘，套构建产物 CSS，用 headless chromium 在 900px 宽度下截图。
  告警条位于 Tab 与统计卡之上，正常换行。
- `scripts/run_gates.py check:quick` 全绿。

## Revisit

若需要知道降级设备的数量或名单，让 watcher-summary 返回逐设备 / 计数口径，
本提示随之改为「N 台设备采集降级」并可下钻。
