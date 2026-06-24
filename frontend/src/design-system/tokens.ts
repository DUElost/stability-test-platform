/**
 * 设计系统 — 语义化布局 / 表面 / 排版令牌
 *
 * 与 index.css CSS 变量对齐，禁止在新代码中使用 gray-* / slate-* / blue-* 硬编码。
 * 图表与 Recharts 仍使用 design-system/colors.ts 中的 CHART_COLORS。
 */

import { cn } from '@/lib/utils';

/** 页面画布、卡片、浮层等表面 */
export const SURFACE = {
  page: 'bg-muted/40',
  elevated: 'bg-card',
  overlay: 'bg-foreground/40',
  subtle: 'bg-muted',
  header: 'bg-card/80 backdrop-blur-sm',
} as const;

/** 边框 */
export const BORDER = {
  default: 'border-border',
  subtle: 'border-border/60',
} as const;

/** 排版 */
export const TEXT = {
  heading: 'text-foreground',
  body: 'text-foreground',
  subtitle: 'text-muted-foreground',
  caption: 'text-muted-foreground',
  subtle: 'text-muted-foreground/80',
  onPrimary: 'text-primary-foreground',
  destructive: 'text-destructive',
} as const;

/** 交互态（悬停 / 聚焦背景） */
export const INTERACTIVE = {
  hover: 'hover:bg-accent',
  hoverText: 'hover:text-foreground',
  menuItem: 'text-muted-foreground hover:bg-accent hover:text-foreground',
  iconButton: 'text-muted-foreground hover:text-foreground',
  destructiveMenu: 'text-destructive hover:bg-destructive/10',
} as const;

/** 侧栏导航 */
export function navLinkClass(active: boolean, collapsed?: boolean): string {
  return cn(
    'w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm transition-all duration-200 group',
    active
      ? 'bg-accent text-foreground font-medium'
      : cn('text-muted-foreground', INTERACTIVE.hover, INTERACTIVE.hoverText),
    collapsed && 'justify-center px-2',
  );
}

export function navIconClass(active: boolean): string {
  return cn(
    'w-4 h-4 flex-shrink-0 transition-colors',
    active ? 'text-foreground' : 'text-muted-foreground group-hover:text-foreground',
  );
}

/** 下划线 Tab（PlanRun 详情等） */
export function tabLinkClass(active: boolean): string {
  return cn(
    'inline-flex items-center border-b-2 px-3 py-2 text-sm font-medium transition-colors',
    active
      ? 'border-primary text-primary'
      : 'border-transparent text-muted-foreground hover:text-foreground',
  );
}

/** 区块标题色条（SectionHeader） */
export const SECTION_ACCENT = {
  primary: 'from-primary to-primary/60',
  success: 'from-success to-success/60',
  warning: 'from-warning to-warning/60',
  destructive: 'from-destructive to-destructive/60',
  muted: 'from-muted-foreground/50 to-muted-foreground/25',
} as const;

/** @deprecated 使用 SECTION_ACCENT 语义名；保留旧 color prop 映射 */
export const SECTION_ACCENT_LEGACY: Record<string, keyof typeof SECTION_ACCENT> = {
  blue: 'primary',
  green: 'success',
  amber: 'warning',
  red: 'destructive',
  gray: 'muted',
};

/** 页面容器 */
export const LAYOUT = {
  pagePadding: 'p-4 lg:p-8',
  pageGap: 'space-y-6',
  pageEnter: 'page-enter',
} as const;

/** 阴影（与 Tailwind 默认阶梯一致，集中引用便于全局调整） */
export const ELEVATION = {
  sm: 'shadow-sm',
  md: 'shadow-md',
  lg: 'shadow-lg',
  dropdown: 'shadow-lg border border-border',
} as const;

/** 仪表盘 / KPI 统计卡 */
export const STAT = {
  label: 'text-xs text-muted-foreground uppercase tracking-wider',
  value: 'text-2xl font-bold text-foreground',
  suffix: 'text-xs text-muted-foreground',
  iconWell: 'w-12 h-12 rounded-xl flex items-center justify-center',
  iconWellMuted: 'bg-muted text-muted-foreground',
  iconWellPrimary: 'bg-primary/10 text-primary',
  iconWellSuccess: 'bg-success/10 text-success',
  iconWellDestructive: 'bg-destructive/10 text-destructive',
} as const;

/** KPI 网格数值强调色（PlanRun 详情等） */
export const KPI_TONE = {
  default: {
    value: 'text-foreground font-bold',
    label: 'text-muted-foreground',
  },
  primary: {
    value: 'text-primary font-bold',
    label: 'text-primary/80',
  },
  success: {
    value: 'text-success font-bold',
    label: 'text-success/80',
  },
  warning: {
    value: 'text-warning font-bold',
    label: 'text-warning/80',
  },
  destructive: {
    value: 'text-destructive font-bold',
    label: 'text-destructive/80',
  },
  info: {
    value: 'text-info font-bold',
    label: 'text-info/80',
  },
} as const;

export type KpiTone = keyof typeof KPI_TONE;

/** 图表区块标题 */
export const CHART_SECTION = {
  title: 'text-lg font-semibold text-foreground',
  subtitle: 'text-sm font-medium text-foreground',
  icon: 'text-muted-foreground',
} as const;

/** 横幅提示（Watcher 阈值、派发门禁 stale 等） */
export const ALERT_BANNER = {
  destructive: 'border-b border-destructive/25 bg-destructive/10 text-destructive',
  warning: 'border-b border-warning/25 bg-warning/10 text-warning',
} as const;

/** Watcher 异常类别左边框着色 */
export const WATCHER_CATEGORY = {
  AEE: 'border-destructive/40 bg-destructive/5',
  VENDOR_AEE: 'border-destructive/40 bg-destructive/5',
  ANR: 'border-warning/40 bg-warning/5',
  TOMBSTONE: 'border-info/40 bg-info/5',
  MOBILELOG: 'border-primary/40 bg-primary/5',
  default: 'border-border bg-muted/50',
} as const;

/** 小型状态 Chip */
export const STATUS_CHIP = {
  destructive: 'bg-destructive/10 text-destructive',
  warning: 'bg-warning/10 text-warning',
  success: 'bg-success/10 text-success',
  primary: 'bg-primary/10 text-primary',
  muted: 'bg-muted text-muted-foreground',
} as const;

/** 分段选择器（时间窗口等） */
export const SEGMENTED = {
  track: 'flex items-center gap-1 rounded-md border bg-card p-0.5 text-xs',
  item: 'rounded px-2 py-0.5 text-muted-foreground hover:bg-accent transition-colors',
  itemActive: 'rounded px-2 py-0.5 bg-primary/10 text-primary',
  toggleActive: 'bg-primary/10 text-primary',
  toggleIdle: 'text-muted-foreground hover:bg-accent hover:text-foreground',
} as const;

/** 趋势箭头着色 */
export const TREND = {
  up: 'text-destructive',
  down: 'text-success',
  flat: 'text-muted-foreground',
} as const;

export const DEDUP_STATUS_CHIP: Record<string, string> = {
  pending: STATUS_CHIP.muted,
  scanned: STATUS_CHIP.primary,
  merged: STATUS_CHIP.success,
};

export const SCRIPT_MATCH_ROW = {
  ok: 'bg-success/10 text-success',
  fail: 'bg-destructive/10 text-destructive',
} as const;

export function dedupActionBtnClass(variant: 'primary' | 'success'): string {
  return variant === 'primary'
    ? cn(
        'rounded border border-primary/25 bg-primary/10 px-1.5 py-0.5',
        'text-[10px] font-semibold text-primary hover:bg-primary/15 disabled:opacity-50',
      )
    : cn(
        'rounded border border-success/25 bg-success/10 px-1.5 py-0.5',
        'text-[10px] font-semibold text-success hover:bg-success/15 disabled:opacity-50',
      );
}

/** 卡片容器（PlanRun 详情区块） */
export const PANEL = {
  root: cn('overflow-hidden rounded-xl border bg-card', ELEVATION.sm),
  footer: cn('border-t bg-muted/50 px-4 py-2'),
  sectionLabel: 'text-[11px] text-muted-foreground',
} as const;

/** 筛选 Chip（设备总览 / 时间线事件过滤） */
export const FILTER_CHIP = {
  active: 'bg-primary/10 font-semibold text-primary',
  idle: 'text-muted-foreground hover:bg-accent',
  count: 'text-[11px] text-muted-foreground/70',
  divider: 'mx-2 h-3 w-px bg-border',
} as const;

/** 时间线事件严重度圆点 */
export const EVENT_SEVERITY_DOT = {
  ok: 'bg-success',
  info: 'bg-info',
  warn: 'bg-warning',
  err: 'bg-destructive',
} as const;

/** 时间线事件阶段 Chip */
export const EVENT_STAGE_CHIP = {
  trigger: 'border-primary/20 bg-primary/10 text-primary',
  init: 'border-info/20 bg-info/10 text-info',
  patrol: 'border-warning/20 bg-warning/10 text-warning',
  teardown: 'border-border bg-muted text-muted-foreground',
  system: 'border-border bg-muted/80 text-muted-foreground',
} as const;

/** 时间线左侧步骤节点 */
export const TIMELINE_NODE = {
  idle: {
    node: 'border-border text-muted-foreground bg-card',
    card: 'border-border bg-card',
    badge: 'bg-muted text-muted-foreground',
  },
  running: {
    node: 'border-warning text-primary-foreground bg-warning',
    card: 'border-warning/50 bg-gradient-to-b from-warning/10 to-card ring-2 ring-warning/25',
    badge: 'bg-warning/10 text-warning',
  },
  success: {
    node: 'border-success text-success bg-success/10',
    card: 'border-success/30 bg-success/5',
    badge: 'bg-success/10 text-success',
  },
  failed: {
    node: 'border-destructive text-destructive bg-destructive/10',
    card: 'border-destructive/30 bg-destructive/5',
    badge: 'bg-destructive/10 text-destructive',
  },
  precheck: {
    node: 'border-info text-info bg-info/10',
    card: 'border-info/30 bg-info/5',
    badge: 'bg-info/10 text-info',
    connector: 'bg-info/40',
  },
  skipped: {
    node: 'border-border text-muted-foreground bg-muted',
    card: 'border-border bg-muted/30',
  },
  active: 'ring-2 ring-primary shadow-md',
  hover: 'hover:bg-accent hover:border-border hover:shadow-md',
  connectorInit: 'bg-success/40',
  connectorPatrol: 'bg-warning/40',
} as const;

/** 时间线右侧步骤明细行 */
export const TIMELINE_STEP_ROW = {
  root: 'grid grid-cols-[60px_16px_1fr_auto] items-start gap-2 border-b border-border/50 bg-primary/5 px-3 py-2.5 text-xs last:border-b-0 hover:bg-primary/10',
  label: 'pt-0.5 text-[11px] font-semibold text-primary',
  icon: 'h-3 w-3 text-primary/70',
} as const;

/** 分段深色选中（异常仪表盘时间范围） */
export const SEGMENTED_DARK = {
  track: 'flex flex-wrap gap-1',
  itemActive: 'rounded-full border border-foreground bg-foreground px-2.5 py-1 text-[11px] font-semibold text-background',
  item: 'rounded-full border bg-card px-2.5 py-1 text-[11px] font-semibold text-muted-foreground hover:border-border hover:text-foreground',
} as const;

/** 仪表盘 KPI 摘要卡 */
export const DASHBOARD_SUMMARY_CARD = {
  root: 'rounded-2xl border bg-card px-4 py-3 shadow-sm',
  label: 'text-[11px] uppercase tracking-[0.16em] text-muted-foreground',
  panel: 'rounded-[24px] border bg-card p-4 shadow-sm',
  sectionMuted: 'rounded-[24px] border bg-muted/50 p-4',
} as const;

/** 包名榜行状态 */
export const PACKAGE_ROW = {
  active: 'border-border bg-muted ring-1 ring-border',
  unknown: 'border-dashed border-border bg-muted/30 hover:bg-muted/50',
  default: 'border bg-card hover:bg-muted/50 hover:border-border',
} as const;

export function packageRankClass(index: number): string {
  if (index === 0) return 'text-warning font-bold text-sm';
  if (index === 1) return 'text-muted-foreground font-bold text-sm';
  if (index === 2) return 'text-warning/80 font-semibold';
  return 'text-muted-foreground/70';
}

/** 侧滑抽屉 */
export const DRAWER = {
  overlay: 'fixed inset-0 z-30 bg-foreground/30 backdrop-blur-sm',
  panel: 'fixed inset-y-0 right-0 z-40 flex w-full max-w-md flex-col overflow-hidden border-l bg-card shadow-2xl focus:outline-none',
  closeBtn: 'rounded-lg p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground',
} as const;

/** 业务流 Stepper 阶段节点 */
export const STEPPER_STAGE = {
  done: { border: 'border-success/30', bg: 'bg-success/5', icon: 'text-success' },
  running: { border: 'border-warning/30 ring-1 ring-warning/20', bg: 'bg-warning/5', icon: 'text-warning' },
  failed: { border: 'border-destructive/30', bg: 'bg-destructive/5', icon: 'text-destructive' },
  pending: { border: 'border-border', bg: 'bg-card', icon: 'text-muted-foreground/40' },
  current: { border: 'border-primary/30', bg: 'bg-card', icon: 'text-primary' },
} as const;
