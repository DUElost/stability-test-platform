# #1962 终态 PlanRun 的仪表盘窗口丢弃「延后落库」的本轮事件

Status: implemented
Class: bug-fix

## Decision

「日志事件归档（DLE）有行、异常仪表盘 0」有**两个独立成因**：#1956 修的是类别口径
（`category IN ('AEE','VENDOR_AEE','ANR')` 漏 UNIVIEW），本单修**时间窗口**。

`_resolve_watcher_summary_window` 对终态 run 取 `window_end = pr.ended_at`，而
`_load_deduped_aee_events` 以 `detected_at ∈ [cur_start, window_end]` 过滤 ——
**本轮自己的事件若在 run 结束之后才落库就被整片丢弃**。这不是罕见路径：reconciler
的首个 tick 需要 `ls` + `pull` 若干事件目录，可能慢于 job 本身的生命周期
（实测 run 393 的 job 只跑 11 秒，其 UNIVIEW 事件在 **70 秒后**才落库），而
DLE 视图按 `plan_run_id` 取数、不受窗口限制，于是两边不一致。

改为：终态 run 的窗口末端加 `LATE_EVENT_GRACE`（30 分钟），并让 `window_end_at`
**如实报出加宽后的窗口**（所见即所计）。宽限值直接复用 `device_log_event` 中
既有的 `_ASSOCIATE_GRACE` —— 该常量本就是为「PlanRun 窗口附近的迟到落库」而设，
两处口径本就应当一致（新公开名 `LATE_EVENT_GRACE`，避免再各写一份而漂移）。

`prev_start`（趋势基线）仍在加宽限**之前**推算，避免放宽末端后基线漂移。

## Alternatives

- **只改 `_load_deduped_aee_events` 的调用点**：`window_end` 还被平台分桶、归档汇总、
  `aee_breakdown` 等共用（`plan_runs.py` 内多处），分别放宽会再次产生口径漂移 —— 故在
  窗口解析处统一放宽。
- **不加宽限，改为 UI 提示「另有 N 条窗口外事件」**：需要新的计数面与文案，且用户仍要
  自己加总；先按「窗口容纳本轮事件」处理，若 owner 更认可提示式口径，改动很小。
- **宽限取更大值（如 2 小时）**：事件已按 `job_id ∈ 本轮 jobs` 过滤，不会串轮，但过大的
  窗口会让「终态 run 的窗口」失去时间意义；30 分钟与既有常量一致，先取此值。

## Verification

**真实数据**（live 库，修复后的解析函数直接复算）：

```text
run 393: 窗口 16:52:16 → 17:22:35（+30min）→ total_events=1    （修复前 0）
run 394: 窗口 16:53:41 → 17:35:45          → total_events=3    （不变）
```

修复后两个 run 的仪表盘计数都与 DLE 视图一致。

**用例** `backend/tests/api/test_plan_run_window_grace_1962.py`：

- 宽限内（ended+70s）的迟到事件**计入**；超出宽限（ended+45min）**不得计入**（反例，
  防止把窗口放宽成无界从而串轮）；
- RUNNING run（无 `ended_at`）窗口末端仍是 `now`，不受宽限影响。

**反例实证**：移除宽限 → `assert 0 == 1` 转红，恢复后 2 passed。

**回归**：`test_plan_run_window_grace_1962 / test_plan_run_aggregation_endpoints /
test_watcher_summary_uniview_1956 / test_agent_api_watcher / test_plan_run_log_events /
test_signal_link_reconciler` 共 **87 passed**。

## Revisit

- 宽限统一适用于终态 run 的所有时间范围（含 `time_scope=15m/1h`）。对短范围而言等效
  窗口会变长（15m → 45m），若产品希望「相对窗口严格按请求范围」，应改为只对
  `window_end == ended_at` 的场景放宽。
- 30 分钟是沿用既有常量；若实测 reconciler 首 tick 偶发更慢（大目录 pull），
  应改为按事件落库延迟的观测值调整，而不是继续手调常量。
- 本次**未**改动 DLE 视图（其按 `plan_run_id` 取数已是正确口径）。
