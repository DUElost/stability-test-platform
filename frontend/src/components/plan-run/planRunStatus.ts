import {
  CheckCircle,
  XCircle,
  AlertTriangle,
  Loader2,
  Hourglass,
  type LucideIcon,
} from "lucide-react";
import type { PlanRun, PlanRunStatus } from "@/utils/api/types";

export const TERMINAL_STATUSES: ReadonlyArray<PlanRunStatus> = [
  "SUCCESS",
  "PARTIAL_SUCCESS",
  "FAILED",
];

export interface PlanRunStatusPill {
  label: string;
  Icon: LucideIcon;
}

export const PLAN_RUN_PILL: Record<PlanRunStatus, PlanRunStatusPill> = {
  QUEUED: { label: "QUEUED", Icon: Hourglass },
  PRECHECK: { label: "PRECHECK", Icon: Loader2 },
  RUNNING: { label: "RUNNING", Icon: Loader2 },
  SUCCESS: { label: "SUCCESS", Icon: CheckCircle },
  PARTIAL_SUCCESS: { label: "PARTIAL", Icon: AlertTriangle },
  FAILED: { label: "FAILED", Icon: XCircle },
};

export function isPlanRunTerminal(status: PlanRunStatus | undefined | null): boolean {
  return !!status && TERMINAL_STATUSES.includes(status);
}

/** ADR-0065 D1：Run/Job 统一「收尾中」展示名。 */
export const FINISHING_UI_LABEL = "收尾中";

/** Job 级 finishing_kind → 中文（仅设备表 / Job 详情抽屉）。 */
export const FINISHING_KIND_LABELS: Record<string, string> = {
  teardown_disarm: "解除配置中",
  atomic_commit: "刷机临界区保护中",
};

/** Job 级 finishing_trigger → 中文。 */
export const FINISHING_TRIGGER_LABELS: Record<string, string> = {
  manual_abort: "手动中止",
  manual_exit: "手动退出",
  natural_end: "自然到点",
  body_failure: "主体失败",
  barrier_timeout: "屏障超时",
};

export function finishingKindLabel(kind: string | null | undefined): string | null {
  if (!kind) return null;
  return FINISHING_KIND_LABELS[kind] ?? kind;
}

export function finishingTriggerLabel(trigger: string | null | undefined): string | null {
  if (!trigger) return null;
  return FINISHING_TRIGGER_LABELS[trigger] ?? trigger;
}

/** Run 级 finishing_kind_counts 摘要，例如「解除配置中 ×2 · 刷机临界区保护中 ×1」。 */
export function formatFinishingKindCounts(
  counts: PlanRun["finishing_kind_counts"] | null | undefined,
): string | null {
  if (!counts) return null;
  const parts: string[] = [];
  for (const [kind, n] of Object.entries(counts)) {
    if (typeof n !== "number" || n <= 0) continue;
    parts.push(`${finishingKindLabel(kind) ?? kind} ×${n}`);
  }
  return parts.length > 0 ? parts.join(" · ") : null;
}

/**
 * 基于 finishing_deadline_at 计算剩余秒；deadline 为 null/无效时返回 null
 *（不得渲染假倒计时，ADR-0065 D1.8）。
 */
export function finishingDeadlineRemainingSeconds(
  deadlineAt: string | null | undefined,
  nowMs: number = Date.now(),
): number | null {
  if (deadlineAt == null || deadlineAt === "") return null;
  const t = new Date(deadlineAt).getTime();
  if (Number.isNaN(t)) return null;
  return Math.max(0, Math.ceil((t - nowMs) / 1000));
}

/** 倒计时文案；无截止时返回 null（调用方不得渲染假倒计时）。 */
export function formatFinishingDeadlineCountdown(
  deadlineAt: string | null | undefined,
  nowMs: number = Date.now(),
): string | null {
  const remaining = finishingDeadlineRemainingSeconds(deadlineAt, nowMs);
  if (remaining == null) return null;
  if (remaining <= 0) return "已到期";
  return `${remaining}s`;
}

export function isPlanRunUiFinishing(run: Pick<PlanRun, "ui_status"> | null | undefined): boolean {
  return run?.ui_status === "finishing";
}

/** 直接判断 ui_status 字符串（列表行 / 详情横幅）。 */
export function isUiStatusFinishing(uiStatus: string | null | undefined): boolean {
  return uiStatus === "finishing";
}

/** @deprecated alias — PlanRunDetailPage 导入名 */
export const formatFinishingKindCountsSummary = formatFinishingKindCounts;
