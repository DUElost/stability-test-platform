import {
  CheckCircle,
  XCircle,
  AlertTriangle,
  Loader2,
  Hourglass,
  ShieldCheck,
  type LucideIcon,
} from "lucide-react";
import type { PlanRun, PlanRunStatus } from "@/utils/api/types";
import { formatDateTimeFull, parseIsoToDate } from "@/utils/format";

export const TERMINAL_STATUSES: ReadonlyArray<PlanRunStatus> = [
  "SUCCESS",
  "PARTIAL_SUCCESS",
  "FAILED",
];

export interface PlanRunStatusPill {
  label: string;
  Icon: LucideIcon;
}

export const PLAN_RUN_PILL: Record<PlanRunStatus | "finishing", PlanRunStatusPill> = {
  QUEUED: { label: "QUEUED", Icon: Hourglass },
  PRECHECK: { label: "PRECHECK", Icon: Loader2 },
  RUNNING: { label: "运行中", Icon: Loader2 },
  finishing: { label: "收尾中", Icon: ShieldCheck },
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

export function getRunFinishingSummary(run: PlanRun | undefined) {
  const groups = Object.entries(run?.finishing_kind_counts ?? {}).flatMap(([kind, count]) => {
    if (count == null || count <= 0) return [];
    const label = kind === "teardown_disarm" ? "解除配置" : kind === "atomic_commit" ? "刷机临界区保护" : "其他";
    return [`${label} ×${count}`];
  });
  const deadline = run?.finishing_deadline_at;
  return {
    counts: groups.length > 0
      ? `${run?.ui_status === "running" ? "部分设备收尾中" : "剩余设备"}：${groups.join(" · ")}`
      : null,
    deadline: parseIsoToDate(deadline) ? `最近截止 ${formatDateTimeFull(deadline)}` : null,
  };
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
  const deadline = parseIsoToDate(deadlineAt);
  if (!deadline) return null;
  return Math.max(0, Math.ceil((deadline.getTime() - nowMs) / 1000));
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

export function getPlanRunDisplayStatus(run: PlanRun): PlanRunStatus | "finishing" {
  if (run.ui_status === "finishing") return "finishing";
  if (run.ui_status === "running") return "RUNNING";
  return run.status;
}
