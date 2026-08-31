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
    running: STATUS_TEXT_COLORS.warning,   // 运行中 - 琥珀（与 StatusBadge 各 kind 的 RUNNING=warning 统一，#356）
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
 * 图表色板
 * 用于 Recharts 等数据可视化库
 */
export const CHART_COLORS = {
  primary: 'hsl(263.7, 90.4%, 54.9%)',     // --primary（葡萄紫）
  success: 'hsl(154.9, 100%, 37.5%)',      // --success
  warning: 'hsl(41, 100%, 45%)',           // --warning
  error: 'hsl(346.8, 77.2%, 45%)',         // --destructive（L 下调过 AA）
  info: 'hsl(199, 89%, 48%)',              // --info（有意保留蓝）
  muted: 'hsl(197.1, 53.8%, 2.5%, 0.69)',  // --muted-foreground（浅色通道）

  // 多系列色板：前 5 来自 OR stats 页实测（非 shadcn chart-1..5 oklch）；
  // 第 6 紫保留 —— DeviceMetricsChart 直取 palette[5]（CPU），不可缩到 5 色。
  palette: [
    '#0088fe',
    '#00c49f',
    '#ffbb28',
    '#ff8042',
    'tomato',
    '#8b5cf6',
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
  QUEUED: 'border-border bg-gradient-to-br from-muted/40 to-card',
  PRECHECK: 'border-info/25 bg-gradient-to-br from-info/10 to-card',
  RUNNING: 'border-primary/25 bg-gradient-to-br from-primary/10 to-card',
  SUCCESS: 'border-success/25 bg-gradient-to-br from-success/10 to-card',
  PARTIAL_SUCCESS: 'border-warning/25 bg-gradient-to-br from-warning/10 to-card',
  FAILED: 'border-destructive/25 bg-gradient-to-br from-destructive/10 to-card',
} as const;

export const PLAN_RUN_HERO_BADGE = {
  QUEUED: 'border-border bg-card text-muted-foreground',
  PRECHECK: 'border-info/40 bg-card text-info',
  RUNNING: 'border-primary/40 bg-card text-primary',
  SUCCESS: 'border-success/40 bg-card text-success',
  PARTIAL_SUCCESS: 'border-warning/40 bg-card text-warning',
  FAILED: 'border-destructive/40 bg-card text-destructive',
} as const;

/** PlanRun Topbar 状态胶囊（含 ring） */
export const PLAN_RUN_STATUS_PILL: Record<PlanRunHeroStatus, string> = {
  QUEUED: 'bg-muted/40 text-muted-foreground ring-border',
  PRECHECK: 'bg-info/10 text-info ring-info/30',
  RUNNING: 'bg-warning/10 text-warning ring-warning/30',
  SUCCESS: 'bg-success/10 text-success ring-success/30',
  PARTIAL_SUCCESS: 'bg-warning/10 text-warning ring-warning/30',
  FAILED: 'bg-destructive/10 text-destructive ring-destructive/30',
} as const;

export type PlanRunHeroStatus = keyof typeof PLAN_RUN_HERO_SURFACE;

/**
 * AEE / Vendor AEE 子类型饼图色板
 * 与初筛选语义对齐；Recharts 需 hex/hsl 字面值，故保留固定色值。
 */
export const AEE_SUBTYPE_CHART_COLORS: Record<string, string> = {
  ANR: '#5b74c8',
  JE: '#ffc94d',
  NE: '#f26363',
  SWT: '#67c7df',
  'Fatal NE': '#f08a52',
  'Fatal JE': '#8b68d6',
  'Combo EE': '#4bb5a8',
  'Kernel API Dump': '#7b879b',
  'System API Dump': '#55a8f2',
  HWT: '#8acb69',
  HANG: '#94a3b8',
  KE: '#6b7280',
  'HW Reboot': '#a3cf5b',
  'Modem EE': '#4d87da',
  'OCP Reboot': '#b082ef',
  'Vendor 其他': '#b7c1d4',
  其他: '#d8dee8',
} as const;

export function aeeSubtypeChartColor(subtype: string): string {
  return AEE_SUBTYPE_CHART_COLORS[subtype] ?? AEE_SUBTYPE_CHART_COLORS['其他'];
}
