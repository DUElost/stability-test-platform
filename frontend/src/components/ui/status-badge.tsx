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
  Zap,
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
  | "host"
  | "job"
  | "plan-run"
  | "risk"
  | "priority";

const DEVICE: Record<string, StatusEntry> = {
  ONLINE: { label: "在线", variant: "success", Icon: CheckCircle2 },
  BUSY: { label: "占用", variant: "warning", Icon: Activity },
  OFFLINE: { label: "离线", variant: "secondary", Icon: PowerOff },
};

const DEVICE_UI: Record<string, StatusEntry> = {
  IDLE: { label: "空闲", variant: "success", Icon: CheckCircle2 },
  TESTING: { label: "测试中", variant: "info", Icon: Zap },
  OFFLINE: { label: "离线", variant: "secondary", Icon: PowerOff },
  ERROR: { label: "错误", variant: "destructive", Icon: AlertTriangle },
};

const HOST: Record<string, StatusEntry> = {
  ONLINE: { label: "在线", variant: "success", Icon: CheckCircle2 },
  DEGRADED: { label: "降级", variant: "warning", Icon: AlertTriangle },
  OFFLINE: { label: "离线", variant: "secondary", Icon: PowerOff },
};

const JOB: Record<string, StatusEntry> = {
  PENDING: { label: "等待", variant: "secondary", Icon: Clock },
  RUNNING: { label: "运行中", variant: "info", Icon: Loader2 },
  COMPLETED: { label: "完成", variant: "success", Icon: CheckCircle2 },
  FAILED: { label: "失败", variant: "destructive", Icon: XCircle },
  ABORTED: { label: "中止", variant: "destructive", Icon: Ban },
  UNKNOWN: { label: "未知", variant: "secondary", Icon: HelpCircle },
};

const PLAN_RUN: Record<string, StatusEntry> = {
  RUNNING: { label: "运行中", variant: "info", Icon: Loader2 },
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

const REGISTRY: Record<StatusBadgeKind, Record<string, StatusEntry>> = {
  device: DEVICE,
  "device-ui": DEVICE_UI,
  host: HOST,
  job: JOB,
  "plan-run": PLAN_RUN,
  risk: RISK,
  priority: PRIORITY,
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
}

export function resolveStatusEntry(
  kind: StatusBadgeKind,
  status: string | null | undefined,
): StatusEntry {
  if (!status) return FALLBACK;
  const table = REGISTRY[kind];
  const upper = status.toUpperCase();
  return table[upper] ?? FALLBACK;
}

export function StatusBadge({
  kind,
  status,
  showIcon = true,
  size = "md",
  className,
}: StatusBadgeProps) {
  const entry = resolveStatusEntry(kind, status);
  const iconSize = size === "sm" ? 10 : 12;
  const sizeCls = size === "sm" ? "px-2 py-0 text-[10px]" : "";
  return (
    <Badge
      variant={entry.variant}
      className={cn("gap-1", sizeCls, className)}
      data-status={status ?? "UNKNOWN"}
      data-kind={kind}
    >
      {showIcon && <entry.Icon size={iconSize} aria-hidden />}
      <span>{entry.label}</span>
    </Badge>
  );
}
