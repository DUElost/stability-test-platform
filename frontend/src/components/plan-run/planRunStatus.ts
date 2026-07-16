import {
  CheckCircle,
  XCircle,
  AlertTriangle,
  Loader2,
  Hourglass,
  type LucideIcon,
} from "lucide-react";
import type { PlanRunStatus } from "@/utils/api/types";

export const TERMINAL_STATUSES: ReadonlyArray<PlanRunStatus> = [
  "SUCCESS",
  "PARTIAL_SUCCESS",
  "FAILED",
  "DEGRADED",
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
  DEGRADED: { label: "DEGRADED", Icon: AlertTriangle },
};

export function isPlanRunTerminal(status: PlanRunStatus | undefined | null): boolean {
  return !!status && TERMINAL_STATUSES.includes(status);
}
