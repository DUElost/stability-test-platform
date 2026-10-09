import { formatDurationSeconds, type DurationStyle } from '@/utils/format';

/**
 * Plan 级「巡检时长」（`Plan.timeout_seconds` → `pipeline_def.lifecycle.timeout_seconds`）的展示语义。
 *
 * 字段名叫 timeout，但在引擎里它是**巡检时长预算**，不是故障超时：
 * 每台设备在**自己的 init 完成**时开始计时，计满后在下一轮巡检开始前以 `timeout` 结束巡检。
 * 这个终止原因判为**成功**（COMPLETED），之后照常执行 teardown。
 * 语义源：`backend/agent/pipeline_engine.py` 的 `_run_patrol_loop`（每轮开始前检查预算）
 * 与 `success = termination_reason in ("completed", "timeout")`。
 *
 * | 状态 | 引擎行为 | 展示 |
 * |------|----------|------|
 * | 无巡检步骤（`patrol_interval_seconds` 为空） | 不进入巡检循环，本字段不生效 | `无巡检` |
 * | `null` / 缺省 | 不限：巡检持续到手动退出或中止 | `不限` |
 * | `n > 0` | 每台设备巡检约 n 秒后正常结束 | 时长 |
 *
 * 后端写入边界是 `ge=1`（`backend/api/routes/plans.py` 的 `PlanCreate` / `PlanUpdate`），0 存不进去。
 *
 * 独立成模块是因为编辑器、执行页选择与执行前确认三处要用同一套口径——
 * 它们此前各写各的，而且都把这个字段称作「超时」，驾驶舱还解释成「整个 PlanRun 超时后中止」。
 */

export const PATROL_DURATION_LABEL = '巡检时长';

/** 悬浮 / 说明文字：不随取值变化的完整定义。 */
export const PATROL_DURATION_HINT =
  '每台设备完成初始化后开始计时，到点后在下一轮巡检前正常结束（算成功）并执行清理步骤；'
  + '不填 = 不限，巡检持续到手动退出或中止。';

/** 列表 / 摘要里的取值展示（`patrolIntervalSeconds` 为空即无巡检步骤，后端保证两者同生同灭）。 */
export function formatPatrolDuration(
  timeoutSeconds: number | null | undefined,
  patrolIntervalSeconds: number | null | undefined,
  style: DurationStyle = 'precise',
): string {
  if (patrolIntervalSeconds == null) return '无巡检';
  if (timeoutSeconds == null || timeoutSeconds <= 0) return '不限';
  return formatDurationSeconds(timeoutSeconds, style);
}

/** 编辑器里随当前取值给出的一句话结论。 */
export function patrolDurationSummary(
  timeoutSeconds: number | null | undefined,
  hasPatrolSteps: boolean,
): string {
  if (!hasPatrolSteps) return '当前没有巡检步骤，巡检时长不生效。';
  if (timeoutSeconds == null || timeoutSeconds <= 0) {
    return '巡检时长不限：巡检会一直进行，直到手动退出或中止。';
  }
  return `每台设备完成初始化后开始计时，巡检满 ${formatDurationSeconds(timeoutSeconds, 'precise')} `
    + '后在下一轮巡检前正常结束（算成功），随后执行清理步骤。';
}
