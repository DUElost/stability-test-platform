import {
  CheckCircle2,
  XCircle,
  Loader2,
  Clock,
  Activity,
  PowerOff,
  AlertTriangle,
  Ban,
  HelpCircle,
  Hourglass,
  Zap,
  ShieldCheck,
  RefreshCw,
  PauseCircle,
  type LucideIcon,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

type BadgeVariant =
  | "default"
  | "secondary"
  | "destructive"
  | "outline"
  | "success"
  | "warning"
  | "info";

interface StatusEntry {
  label: string;
  variant: BadgeVariant;
  Icon: LucideIcon;
}

export type StatusBadgeKind =
  | "device"
  | "device-ui"
  | "device-link"
  | "host"
  | "job"
  | "job-result"
  | "plan-run"
  | "risk"
  | "priority"
  | "precheck-phase"
  | "precheck-host";

const DEVICE: Record<string, StatusEntry> = {
  ONLINE: { label: "在线", variant: "success", Icon: CheckCircle2 },
  BUSY: { label: "占用", variant: "warning", Icon: Activity },
  OFFLINE: { label: "离线", variant: "secondary", Icon: PowerOff },
  ERROR: { label: "异常", variant: "destructive", Icon: AlertTriangle },
};

const DEVICE_UI: Record<string, StatusEntry> = {
  IDLE: { label: "空闲", variant: "success", Icon: CheckCircle2 },
  TESTING: { label: "测试中", variant: "info", Icon: Zap },
  OFFLINE: { label: "离线", variant: "secondary", Icon: PowerOff },
  ERROR: { label: "错误", variant: "destructive", Icon: AlertTriangle },
  RUNNING: { label: "运行中", variant: "warning", Icon: Loader2 },
  COMPLETED: { label: "完成", variant: "success", Icon: CheckCircle2 },
  FAILED: { label: "失败", variant: "destructive", Icon: XCircle },
  UNKNOWN: { label: "已断开", variant: "warning", Icon: AlertTriangle },
  BACKOFF: { label: "退避", variant: "warning", Icon: Clock },
  PENDING: { label: "等待", variant: "secondary", Icon: PauseCircle },
};

const DEVICE_LINK: Record<string, StatusEntry> = {
  ONLINE: { label: "在线", variant: "success", Icon: CheckCircle2 },
  OFFLINE: { label: "离线", variant: "secondary", Icon: PowerOff },
  ADB_ERROR: { label: "ADB 异常", variant: "destructive", Icon: AlertTriangle },
  HOST_OFFLINE: { label: "Host 离线", variant: "destructive", Icon: PowerOff },
  UNKNOWN: { label: "未知", variant: "secondary", Icon: HelpCircle },
};

const HOST: Record<string, StatusEntry> = {
  ONLINE: { label: "在线", variant: "success", Icon: CheckCircle2 },
  DEGRADED: { label: "降级", variant: "warning", Icon: AlertTriangle },
  OFFLINE: { label: "离线", variant: "secondary", Icon: PowerOff },
};

const JOB: Record<string, StatusEntry> = {
  PENDING: { label: "等待", variant: "secondary", Icon: Clock },
  RUNNING: { label: "运行中", variant: "warning", Icon: Loader2 },
  COMPLETED: { label: "完成", variant: "success", Icon: CheckCircle2 },
  FAILED: { label: "失败", variant: "destructive", Icon: XCircle },
  ABORTED: { label: "中止", variant: "destructive", Icon: Ban },
  UNKNOWN: { label: "未知", variant: "secondary", Icon: HelpCircle },
};

/**
 * Results 页运行状态。键集 = results API 出参映射的全集
 * （backend results.py `_JOB_STATUS_TO_RUN_STATUS` 产出 QUEUED/RUNNING/
 * FINISHED/FAILED/CANCELED）。CANCELED 用中性灰而非 destructive：
 * 主动取消 ≠ 失败（GitHub Actions / GitLab CI 同此约定）。
 * 后端 `_normalize_job_status` 对未识别状态原样透传，消费方应开
 * `fallbackToRaw` 回显原文，避免运维只看到「未知」。
 */
const JOB_RESULT: Record<string, StatusEntry> = {
  QUEUED: { label: "排队中", variant: "secondary", Icon: Hourglass },
  RUNNING: { label: "运行中", variant: "warning", Icon: Loader2 },
  FINISHED: { label: "完成", variant: "success", Icon: CheckCircle2 },
  FAILED: { label: "失败", variant: "destructive", Icon: XCircle },
  CANCELED: { label: "已中止", variant: "secondary", Icon: Ban },
};

const PLAN_RUN: Record<string, StatusEntry> = {
  QUEUED: { label: "排队中", variant: "secondary", Icon: Hourglass },
  PRECHECK: { label: "准入检查", variant: "info", Icon: Loader2 },
  RUNNING: { label: "运行中", variant: "warning", Icon: Loader2 },
  SUCCESS: { label: "成功", variant: "success", Icon: CheckCircle2 },
  PARTIAL_SUCCESS: { label: "部分成功", variant: "warning", Icon: AlertTriangle },
  FAILED: { label: "失败", variant: "destructive", Icon: XCircle },
  DEGRADED: { label: "降级", variant: "warning", Icon: AlertTriangle },
};

const RISK: Record<string, StatusEntry> = {
  HIGH: { label: "高", variant: "destructive", Icon: AlertTriangle },
  MEDIUM: { label: "中", variant: "warning", Icon: AlertTriangle },
  LOW: { label: "低", variant: "success", Icon: CheckCircle2 },
  UNKNOWN: { label: "未知", variant: "secondary", Icon: HelpCircle },
};

const PRIORITY: Record<string, StatusEntry> = {
  CRITICAL: { label: "Critical", variant: "destructive", Icon: AlertTriangle },
  MAJOR: { label: "Major", variant: "warning", Icon: AlertTriangle },
  MINOR: { label: "Minor", variant: "info", Icon: CheckCircle2 },
};

const PRECHECK_PHASE: Record<string, StatusEntry> = {
  VERIFYING: { label: "校验中", variant: "info", Icon: ShieldCheck },
  SYNCING: { label: "同步中", variant: "warning", Icon: RefreshCw },
  REVERIFYING: { label: "再校验", variant: "info", Icon: ShieldCheck },
  READY: { label: "门禁通过", variant: "success", Icon: CheckCircle2 },
  FAILED: { label: "门禁失败", variant: "destructive", Icon: XCircle },
};

const PRECHECK_HOST: Record<string, StatusEntry> = {
  PENDING: { label: "待检查", variant: "secondary", Icon: Loader2 },
  OK: { label: "一致", variant: "success", Icon: CheckCircle2 },
  SYNCING: { label: "同步中", variant: "warning", Icon: RefreshCw },
  SYNCED: { label: "已同步", variant: "info", Icon: CheckCircle2 },
  FAILED: { label: "失败", variant: "destructive", Icon: XCircle },
};

const REGISTRY: Record<StatusBadgeKind, Record<string, StatusEntry>> = {
  device: DEVICE,
  "device-ui": DEVICE_UI,
  "device-link": DEVICE_LINK,
  host: HOST,
  job: JOB,
  "job-result": JOB_RESULT,
  "plan-run": PLAN_RUN,
  risk: RISK,
  priority: PRIORITY,
  "precheck-phase": PRECHECK_PHASE,
  "precheck-host": PRECHECK_HOST,
};

const FALLBACK: StatusEntry = {
  label: "未知",
  variant: "secondary",
  Icon: HelpCircle,
};

export interface StatusBadgeProps {
  kind: StatusBadgeKind;
  status: string | null | undefined;
  showIcon?: boolean;
  size?: "sm" | "md";
  className?: string;
  /** Add `animate-spin` to the icon. Use for in-progress loaders. */
  spin?: boolean;
  /**
   * 未命中且状态非空时回显原文（灰色 + HelpCircle），而非「未知」。
   * 用于后端原样透传未知状态的场景（如 results 的 `_normalize_job_status`）。
   */
  fallbackToRaw?: boolean;
}

export interface ResolveStatusOptions {
  fallbackToRaw?: boolean;
}

export function resolveStatusEntry(
  kind: StatusBadgeKind,
  status: string | null | undefined,
  { fallbackToRaw = false }: ResolveStatusOptions = {},
): StatusEntry {
  if (!status) return FALLBACK;
  const table = REGISTRY[kind];
  const upper = status.toUpperCase();
  const entry = table[upper];
  if (entry) return entry;
  if (fallbackToRaw) {
    return { label: status, variant: FALLBACK.variant, Icon: FALLBACK.Icon };
  }
  return FALLBACK;
}

export function StatusBadge({
  kind,
  status,
  showIcon = true,
  size = "md",
  className,
  spin = false,
  fallbackToRaw = false,
}: StatusBadgeProps) {
  const entry = resolveStatusEntry(kind, status, { fallbackToRaw });
  const iconSize = size === "sm" ? 10 : 12;
  const sizeCls = size === "sm" ? "px-2 py-0 text-[10px]" : "";
  return (
    <Badge
      variant={entry.variant}
      className={cn("gap-1", sizeCls, className)}
      data-status={status ?? "UNKNOWN"}
      data-kind={kind}
    >
      {showIcon && (
        <entry.Icon
          size={iconSize}
          className={spin ? "animate-spin" : undefined}
          aria-hidden
        />
      )}
      <span>{entry.label}</span>
    </Badge>
  );
}
