/**
 * 步骤墙钟（`step.timeout_seconds`）的展示语义。
 *
 * 契约里这个字段的三种取值行为完全不同，UI 必须分开呈现：
 *
 * | 值 | 引擎行为 | 展示 |
 * |----|----------|------|
 * | `0` | 无上限，`communicate(timeout=None)` 一直等到子进程退出 | `∞` |
 * | `null` / 缺省 | 回落 `STP_STEP_WALL_CLOCK_SECONDS`，再回落 300s | `默认` |
 * | `n > 0` | n 秒墙钟 | `ns` |
 *
 * 语义源：`backend/agent/pipeline_engine.py::_resolve_step_wall_clock`
 * 与 `backend/schemas/pipeline_schema.json` §step。
 *
 * 独立成模块是因为画布行与 Inspector 两处都要用同一套判定——它们此前各写各的，
 * 而且**两个方向都反了**：`0` 显示成 `0s`（读起来像"零秒超时"，实际是不限），
 * `null` 显示成 `∞`（读起来像"永不超时"，实际 300s 就会被杀）。
 */

/** 引擎在未配置时的兜底墙钟（`_DEFAULT_STEP_WALL_CLOCK_SECONDS`）。 */
export const DEFAULT_STEP_WALL_CLOCK_SECONDS = 300;

export function formatStepTimeout(seconds: number | null | undefined): string {
  if (seconds === 0) return '∞';
  if (seconds == null) return '默认';
  return `${seconds}s`;
}

/** 悬浮说明：光看 `∞` / `默认` 两个字判断不出背后是哪条规则。 */
export function stepTimeoutHint(seconds: number | null | undefined): string {
  if (seconds === 0) {
    return '不限：无墙钟上限，跑到脚本自己退出为止（契约要求同时配停滞钟 stall_seconds ≥ 1）';
  }
  if (seconds == null) {
    return `未配置：回落 Agent 的 STP_STEP_WALL_CLOCK_SECONDS，未设则 ${DEFAULT_STEP_WALL_CLOCK_SECONDS}s`;
  }
  return `墙钟上限 ${seconds}s，超时判该步骤失败`;
}
