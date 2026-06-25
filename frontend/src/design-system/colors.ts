/**
 * 设计系统 - 色彩常量
 *
 * 基于 index.css 中定义的 CSS 变量，提供语义化的 Tailwind 类名常量。
 * 所有颜色应使用此文件中的常量，而非硬编码颜色值。
 */

/**
 * 状态文字颜色
 * 用于状态文字、图标等前景色
 */
export const STATUS_TEXT_COLORS = {
  primary: 'text-primary',
  success: 'text-success',
  warning: 'text-warning',
  error: 'text-destructive',
  info: 'text-info',
  muted: 'text-muted-foreground',
  default: 'text-foreground',
} as const;

/**
 * 状态背景颜色
 * 用于 Badge、Alert 等组件背景
 */
export const STATUS_BG_COLORS = {
  primary: 'bg-primary/10 text-primary',
  success: 'bg-success/10 text-success',
  warning: 'bg-warning/10 text-warning',
  error: 'bg-destructive/10 text-destructive',
  info: 'bg-info/10 text-info',
  muted: 'bg-muted text-muted-foreground',
} as const;

/**
 * 实体状态映射（设备、主机、任务等）
 */
export const ENTITY_STATUS_COLORS = {
  // 设备状态
  device: {
    idle: STATUS_TEXT_COLORS.success,      // 空闲 - 绿色
    testing: STATUS_TEXT_COLORS.primary,   // 测试中 - 蓝色
    offline: STATUS_TEXT_COLORS.muted,     // 离线 - 灰色
    error: STATUS_TEXT_COLORS.error,       // 错误 - 红色
  },

  // 主机状态
  host: {
    online: STATUS_TEXT_COLORS.success,    // 在线 - 绿色
    offline: STATUS_TEXT_COLORS.muted,     // 离线 - 灰色
    degraded: STATUS_TEXT_COLORS.warning,  // 降级 - 橙色
  },

  // 任务/执行状态
  execution: {
    pending: STATUS_TEXT_COLORS.muted,     // 待执行 - 灰色
    running: STATUS_TEXT_COLORS.primary,   // 运行中 - 蓝色
    success: STATUS_TEXT_COLORS.success,   // 成功 - 绿色
    failed: STATUS_TEXT_COLORS.error,      // 失败 - 红色
    partial: STATUS_TEXT_COLORS.warning,   // 部分成功 - 橙色
  },

  // 告警级别
  alert: {
    none: STATUS_TEXT_COLORS.success,      // 无告警 - 绿色
    low: STATUS_TEXT_COLORS.info,          // 低级 - 蓝色
    medium: STATUS_TEXT_COLORS.warning,    // 中级 - 橙色
    high: STATUS_TEXT_COLORS.error,        // 高级 - 红色
  },
} as const;

/**
 * Badge 背景色映射
 */
export const BADGE_COLORS = {
  // 设备状态
  device: {
    idle: STATUS_BG_COLORS.success,
    testing: STATUS_BG_COLORS.primary,
    offline: STATUS_BG_COLORS.muted,
    error: STATUS_BG_COLORS.error,
  },

  // 主机状态
  host: {
    online: STATUS_BG_COLORS.success,
    offline: STATUS_BG_COLORS.muted,
    degraded: STATUS_BG_COLORS.warning,
  },

  // 任务状态
  execution: {
    pending: STATUS_BG_COLORS.muted,
    running: STATUS_BG_COLORS.primary,
    success: STATUS_BG_COLORS.success,
    failed: STATUS_BG_COLORS.error,
    partial: STATUS_BG_COLORS.warning,
  },

  // 告警级别
  alert: {
    none: STATUS_BG_COLORS.success,
    low: STATUS_BG_COLORS.info,
    medium: STATUS_BG_COLORS.warning,
    high: STATUS_BG_COLORS.error,
  },
} as const;

/**
 * 图表色板
 * 用于 Recharts 等数据可视化库
 */
export const CHART_COLORS = {
  primary: 'hsl(217, 91%, 60%)',           // --primary
  success: 'hsl(142, 71%, 45%)',           // --success
  warning: 'hsl(38, 92%, 50%)',            // --warning
  error: 'hsl(0, 84.2%, 60.2%)',           // --destructive
  info: 'hsl(199, 89%, 48%)',              // --info
  muted: 'hsl(215.4, 16.3%, 46.9%)',       // --muted-foreground

  // 渐变色板（用于多系列图表）
  palette: [
    'hsl(217, 91%, 60%)',   // 蓝
    'hsl(142, 71%, 45%)',   // 绿
    'hsl(38, 92%, 50%)',    // 橙
    'hsl(0, 84.2%, 60.2%)', // 红
    'hsl(199, 89%, 48%)',   // 青
    'hsl(271, 81%, 56%)',   // 紫
  ],
} as const;

/**
 * 辅助函数：根据数值获取对应颜色
 * @example getThresholdColor(95, { warning: 80, error: 90 }) // 'text-destructive'
 */
export function getThresholdColor(
  value: number,
  thresholds: { warning: number; error: number }
): string {
  if (value >= thresholds.error) return STATUS_TEXT_COLORS.error;
  if (value >= thresholds.warning) return STATUS_TEXT_COLORS.warning;
  return STATUS_TEXT_COLORS.success;
}

/**
 * 辅助函数：根据布尔值获取成功/错误颜色
 */
export function getBooleanColor(success: boolean): string {
  return success ? STATUS_TEXT_COLORS.success : STATUS_TEXT_COLORS.error;
}

/**
 * PlanRun Hero 区域 — 与 StatusBadge kind=plan-run 语义对齐
 */
export const PLAN_RUN_HERO_SURFACE = {
  RUNNING: 'border-primary/25 bg-gradient-to-br from-primary/10 to-card',
  SUCCESS: 'border-success/25 bg-gradient-to-br from-success/10 to-card',
  PARTIAL_SUCCESS: 'border-warning/25 bg-gradient-to-br from-warning/10 to-card',
  FAILED: 'border-destructive/25 bg-gradient-to-br from-destructive/10 to-card',
  DEGRADED: 'border-warning/30 bg-gradient-to-br from-warning/15 to-card',
} as const;

export const PLAN_RUN_HERO_BADGE = {
  RUNNING: 'border-primary/40 bg-card text-primary',
  SUCCESS: 'border-success/40 bg-card text-success',
  PARTIAL_SUCCESS: 'border-warning/40 bg-card text-warning',
  FAILED: 'border-destructive/40 bg-card text-destructive',
  DEGRADED: 'border-warning/50 bg-card text-warning',
} as const;

/** PlanRun Topbar 状态胶囊（含 ring） */
export const PLAN_RUN_STATUS_PILL: Record<PlanRunHeroStatus, string> = {
  RUNNING: 'bg-warning/10 text-warning ring-warning/30',
  SUCCESS: 'bg-success/10 text-success ring-success/30',
  PARTIAL_SUCCESS: 'bg-warning/10 text-warning ring-warning/30',
  FAILED: 'bg-destructive/10 text-destructive ring-destructive/30',
  DEGRADED: 'bg-info/10 text-info ring-info/30',
} as const;

export type PlanRunHeroStatus = keyof typeof PLAN_RUN_HERO_SURFACE;
