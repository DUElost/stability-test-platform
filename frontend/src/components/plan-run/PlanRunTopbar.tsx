import { useEffect, useMemo, useState } from 'react';
import { CheckCircle, XCircle, AlertTriangle, Clock, Activity, Loader2 } from 'lucide-react';
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
    cls: 'bg-orange-100 text-orange-800 ring-orange-300',
    Icon: Loader2,
  },
  SUCCESS: {
    label: 'SUCCESS',
    cls: 'bg-green-100 text-green-800 ring-green-300',
    Icon: CheckCircle,
  },
  PARTIAL_SUCCESS: {
    label: 'PARTIAL',
    cls: 'bg-yellow-100 text-yellow-800 ring-yellow-300',
    Icon: AlertTriangle,
  },
  FAILED: {
    label: 'FAILED',
    cls: 'bg-red-100 text-red-800 ring-red-300',
    Icon: XCircle,
  },
  DEGRADED: {
    label: 'DEGRADED',
    cls: 'bg-purple-100 text-purple-800 ring-purple-300',
    Icon: AlertTriangle,
  },
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

interface Props {
  run: PlanRun | undefined;
  planName?: string | null;
  isAborting?: boolean;
  onAbort?: (reason: string) => void;
  onExportReport?: () => void;
  /** Override "now" for deterministic tests. */
  now?: Date;
}

export default function PlanRunTopbar({
  run,
  planName,
  isAborting = false,
  onAbort,
  onExportReport,
  now,
}: Props) {
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [reason, setReason] = useState('');

  // Tick once a second to keep the run-time live for non-terminal runs.
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
    const end = run.ended_at ? new Date(run.ended_at).getTime() : (now ?? new Date()).getTime();
    return formatDuration(Math.max(0, (end - start) / 1000));
    // tick is intentionally listed to refresh non-terminal duration each second.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [run, now, tick]);

  const cfg = run ? STATUS_PILL[run.status] : null;

  return (
    <div className="flex flex-wrap items-center gap-3 rounded-xl border bg-gradient-to-b from-white to-gray-50 px-4 py-2.5 shadow-sm">
      <div className="flex items-center gap-1.5">
        <span className="text-sm font-bold tracking-wide text-blue-600">⬢ STP</span>
      </div>
      <div className="flex flex-wrap items-center gap-1.5 text-xs text-gray-500">
        <span>编排</span>
        <span className="text-gray-300">/</span>
        <span>{planName ? `Plan #${run?.plan_id ?? ''} · ${planName}` : `Plan #${run?.plan_id ?? '-'}`}</span>
        <span className="text-gray-300">/</span>
        <span className="font-semibold text-gray-900">PlanRun #{run?.id ?? '-'}</span>
      </div>

      <div className="ml-auto flex items-center gap-2">
        {cfg && (
          <span
            data-testid="plan-run-status-pill"
            className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-[11px] font-semibold ring-1 ring-inset ${cfg.cls}`}
          >
            <cfg.Icon
              className={`h-3 w-3 ${run?.status === 'RUNNING' ? 'animate-spin' : ''}`}
            />
            {cfg.label}
            {runDuration && (
              <>
                <span className="text-gray-400">·</span>
                <span data-testid="plan-run-duration" className="font-mono">
                  {runDuration}
                </span>
              </>
            )}
          </span>
        )}

        <Button
          variant="outline"
          size="sm"
          onClick={onExportReport}
          disabled={!run}
          className="text-xs"
        >
          <Activity className="mr-1 h-3.5 w-3.5" />
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
              className="text-xs"
            >
              {isAborting ? (
                <>
                  <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" /> 中止中…
                </>
              ) : (
                <>
                  <Clock className="mr-1 h-3.5 w-3.5" /> 中止 PlanRun
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
  );
}
