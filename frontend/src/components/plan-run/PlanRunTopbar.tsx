import { useEffect, useMemo, useState } from 'react';
import { Clock, Activity, Loader2 } from 'lucide-react';
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
import { PLAN_RUN_STATUS_PILL, TEXT } from '@/design-system';
import { cn } from '@/lib/utils';
import type { PlanRun } from '@/utils/api/types';
import { PLAN_RUN_PILL, isPlanRunTerminal } from './planRunStatus';

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

  const [tick, setTick] = useState(0);
  const isTerminal = !!run && isPlanRunTerminal(run.status);
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [run, now, tick]);

  const pill = run ? PLAN_RUN_PILL[run.status] : null;
  const pillCls = run ? PLAN_RUN_STATUS_PILL[run.status as keyof typeof PLAN_RUN_STATUS_PILL] : '';

  return (
    <div className="flex flex-wrap items-center gap-3 rounded-xl border bg-gradient-to-b from-card to-muted/30 px-4 py-2.5 shadow-sm">
      <div className="flex items-center gap-1.5">
        <span className="text-sm font-bold tracking-wide text-primary">⬢ STP</span>
      </div>
      <div className={cn('flex flex-wrap items-center gap-1.5 text-xs', TEXT.subtitle)}>
        <span>编排</span>
        <span className="text-muted-foreground/50">/</span>
        <span>{planName ? `Plan #${run?.plan_id ?? ''} · ${planName}` : `Plan #${run?.plan_id ?? '-'}`}</span>
        <span className="text-muted-foreground/50">/</span>
        <span className={cn('font-semibold', TEXT.heading)}>PlanRun #{run?.id ?? '-'}</span>
      </div>

      <div className="ml-auto flex items-center gap-2">
        {pill && (
          <span
            data-testid="plan-run-status-pill"
            className={cn(
              'inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-[11px] font-semibold ring-1 ring-inset',
              pillCls,
            )}
          >
            <pill.Icon
              className={cn('h-3 w-3', run?.status === 'RUNNING' && 'animate-spin')}
            />
            {pill.label}
            {runDuration && (
              <>
                <span className="text-muted-foreground/70">·</span>
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
                  <label className={cn('block text-sm font-medium', TEXT.body)}>
                    中止原因(可选)
                  </label>
                  <input
                    type="text"
                    value={reason}
                    onChange={(e) => setReason(e.target.value)}
                    placeholder="例如:资源池整改"
                    className="w-full rounded-md border bg-card px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-destructive/30"
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
                    className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
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
