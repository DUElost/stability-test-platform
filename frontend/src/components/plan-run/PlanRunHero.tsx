import { useEffect, useMemo, useState } from 'react';
import {
  CheckCircle,
  XCircle,
  AlertTriangle,
  Clock,
  Activity,
  Loader2,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import type { PlanRun, PlanRunStatus } from '@/utils/api/types';

const TERMINAL_STATUSES: ReadonlyArray<PlanRunStatus> = [
  'SUCCESS',
  'PARTIAL_SUCCESS',
  'FAILED',
  'DEGRADED',
];

const STATUS_PILL: Record<
  PlanRunStatus,
  { label: string; cls: string; Icon: React.ElementType }
> = {
  RUNNING: {
    label: 'RUNNING',
    cls: 'bg-orange-50 text-orange-700 border-orange-200',
    Icon: Loader2,
  },
  SUCCESS: {
    label: 'SUCCESS',
    cls: 'bg-green-50 text-green-700 border-green-200',
    Icon: CheckCircle,
  },
  PARTIAL_SUCCESS: {
    label: 'PARTIAL',
    cls: 'bg-yellow-50 text-yellow-700 border-yellow-200',
    Icon: AlertTriangle,
  },
  FAILED: {
    label: 'FAILED',
    cls: 'bg-red-50 text-red-700 border-red-200',
    Icon: XCircle,
  },
  DEGRADED: {
    label: 'DEGRADED',
    cls: 'bg-purple-50 text-purple-700 border-purple-200',
    Icon: AlertTriangle,
  },
};

const STAGE_LABEL: Record<string, string> = {
  init: 'INIT',
  patrol: 'PATROL',
  teardown: 'TEARDOWN',
};

function formatDuration(seconds: number): string {
  if (!isFinite(seconds) || seconds < 0) return '—';
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}

function formatRelative(ts: string | null | undefined): string {
  if (!ts) return '';
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return '';
  return d.toLocaleTimeString('zh-CN', { hour12: false });
}

export interface PlanRunHeroSummaryStats {
  hostCount?: number;
  deviceCount?: number;
  currentStage?: string | null;
  patrolCycle?: number | null;
  lastSyncTime?: string | null;
}

interface Props {
  run: PlanRun | undefined;
  planName?: string | null;
  isAborting?: boolean;
  onAbort?: (reason: string) => void;
  onExportReport?: () => void;
  summary?: PlanRunHeroSummaryStats;
  /** Override "now" for deterministic tests. */
  now?: Date;
}

export default function PlanRunHero({
  run,
  planName,
  isAborting = false,
  onAbort,
  onExportReport,
  summary,
  now,
}: Props) {
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [reason, setReason] = useState('');

  // Tick once a second for live run-time on non-terminal runs.
  const [tick, setTick] = useState(0);
  const isTerminal = !!run && TERMINAL_STATUSES.includes(run.status);
  useEffect(() => {
    if (isTerminal || now) return;
    const id = window.setInterval(() => setTick((t) => t + 1), 1000);
    return () => window.clearInterval(id);
  }, [isTerminal, now]);

  const runDuration = useMemo(() => {
    if (!run) return null;
    const start = new Date(run.started_at).getTime();
    const end = run.ended_at
      ? new Date(run.ended_at).getTime()
      : (now ?? new Date()).getTime();
    return formatDuration(Math.max(0, (end - start) / 1000));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [run, now, tick]);

  const cfg = run ? STATUS_PILL[run.status] : null;

  const stageStr = summary?.currentStage
    ? (STAGE_LABEL[summary.currentStage] ?? summary.currentStage.toUpperCase())
    : null;
  const cycleStr = summary?.patrolCycle != null && summary.patrolCycle >= 0
    ? `周期 #${summary.patrolCycle}`
    : null;
  const syncStr = summary?.lastSyncTime
    ? `同步 ${formatRelative(summary.lastSyncTime)}`
    : null;

  return (
    <div className="overflow-hidden rounded-xl border bg-gradient-to-b from-white to-gray-50 shadow-sm">
      {/* Main hero row */}
      <div className="grid grid-cols-[1fr_auto] items-start gap-3 px-4 py-3">
        {/* Left: title + meta */}
        <div className="min-w-0">
          {/* Breadcrumb */}
          <div className="mb-0.5 flex flex-wrap items-center gap-1.5 text-[11px] text-gray-400">
            <span className="text-blue-600 font-semibold">
              {planName ? `Plan #${run?.plan_id ?? ''} · ${planName}` : `Plan #${run?.plan_id ?? '-'}`}
            </span>
            <span>/</span>
            <span className="font-semibold text-gray-800">PlanRun #{run?.id ?? '-'}</span>
          </div>

          {/* Status line */}
          <div className="flex flex-wrap items-center gap-2">
            {cfg && (
              <span
                data-testid="plan-run-status-pill"
                className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[11px] font-bold ${cfg.cls}`}
              >
                {run?.status === 'RUNNING' && (
                  <span className="relative flex h-2 w-2">
                    <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-orange-400 opacity-60" />
                    <span className="relative inline-flex h-2 w-2 rounded-full bg-orange-500" />
                  </span>
                )}
                <cfg.Icon className={`h-3 w-3 ${run?.status === 'RUNNING' ? 'animate-spin' : ''}`} />
                {cfg.label}
                {runDuration && (
                  <span data-testid="plan-run-duration" className="ml-0.5 font-mono text-[10.5px] text-gray-500">
                    {runDuration}
                  </span>
                )}
              </span>
            )}

            {/* Meta chips */}
            {stageStr && (
              <span className="inline-flex items-center gap-1 rounded-full border border-gray-200 bg-white px-2 py-0.5 text-[10.5px] font-semibold text-gray-600">
                <span className="h-1.5 w-1.5 rounded-full bg-orange-400" />
                {stageStr}
                {cycleStr && <span className="text-gray-400">· {cycleStr}</span>}
              </span>
            )}
            {syncStr && (
              <span className="inline-flex items-center gap-1 rounded-full border border-gray-200 bg-white px-2 py-0.5 text-[10.5px] font-semibold text-gray-600">
                <Clock className="h-3 w-3 text-gray-400" />
                {syncStr}
              </span>
            )}
            {summary?.hostCount != null && (
              <span className="inline-flex items-center gap-1 rounded-full border border-gray-200 bg-white px-2 py-0.5 text-[10.5px] font-semibold text-gray-600">
                {summary.hostCount} 台主机
              </span>
            )}
            {summary?.deviceCount != null && (
              <span className="inline-flex items-center gap-1 rounded-full border border-gray-200 bg-white px-2 py-0.5 text-[10.5px] font-semibold text-gray-600">
                {summary.deviceCount} 台设备
              </span>
            )}
          </div>
        </div>

        {/* Right: actions */}
        <div className="flex shrink-0 items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={onExportReport}
            disabled={!run}
            className="text-[11px] h-7"
          >
            <Activity className="mr-1 h-3 w-3" />
            导出报告
          </Button>

          {!isTerminal && (
            <>
              <Button
                variant="destructive"
                size="sm"
                data-testid="plan-run-abort-btn"
                onClick={() => setConfirmOpen(true)}
                disabled={!run || isAborting}
                className="text-[11px] h-7"
              >
                {isAborting ? (
                  <>
                    <Loader2 className="mr-1 h-3 w-3 animate-spin" /> 中止中…
                  </>
                ) : (
                  <>
                    <Clock className="mr-1 h-3 w-3" /> 中止
                  </>
                )}
              </Button>

              <AlertDialog open={confirmOpen} onOpenChange={setConfirmOpen}>
                <AlertDialogContent>
                  <AlertDialogHeader>
                    <AlertDialogTitle>确认中止 PlanRun?</AlertDialogTitle>
                    <AlertDialogDescription>
                      将释放运行中设备的租约,PENDING Job 标记为 ABORTED;
                      Agent 上正在运行的 step 会异步收到中止信号(取决于 Agent 协作)。
                      操作不可撤销。
                    </AlertDialogDescription>
                  </AlertDialogHeader>
                  <div className="space-y-2">
                    <label className="block text-sm font-medium text-gray-700">
                      中止原因(可选)
                    </label>
                    <input
                      type="text"
                      value={reason}
                      onChange={(e) => setReason(e.target.value)}
                      placeholder="例如:资源池整改"
                      className="w-full rounded-md border px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-red-500/30"
                    />
                  </div>
                  <AlertDialogFooter>
                    <AlertDialogCancel>取消</AlertDialogCancel>
                    <AlertDialogAction
                      data-testid="plan-run-abort-confirm"
                      onClick={() => {
                        setConfirmOpen(false);
                        onAbort?.(reason.trim() || 'aborted_by_user');
                      }}
                      className="bg-red-600 text-white hover:bg-red-700"
                    >
                      确认中止
                    </AlertDialogAction>
                  </AlertDialogFooter>
                </AlertDialogContent>
              </AlertDialog>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
