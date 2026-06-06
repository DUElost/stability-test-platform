import { useEffect, useMemo, useState } from 'react';
import { Download, X, Loader2, ChevronDown } from 'lucide-react';
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
import { PLAN_RUN_PILL, isPlanRunTerminal } from './planRunStatus';

// 状态 → 容器背景/边框
const HERO_CLS: Record<PlanRunStatus, string> = {
  RUNNING:        'border-orange-200 bg-gradient-to-br from-orange-50/80 to-white',
  SUCCESS:        'border-green-200  bg-gradient-to-br from-green-50/60  to-white',
  PARTIAL_SUCCESS:'border-yellow-200 bg-gradient-to-br from-yellow-50/60 to-white',
  FAILED:         'border-red-200    bg-gradient-to-br from-red-50/60    to-white',
  DEGRADED:       'border-purple-200 bg-gradient-to-br from-purple-50/60 to-white',
};

// 状态 → badge 样式
const BADGE_CLS: Record<PlanRunStatus, string> = {
  RUNNING:        'border-orange-300 bg-white text-orange-700',
  SUCCESS:        'border-green-300  bg-white text-green-700',
  PARTIAL_SUCCESS:'border-yellow-300 bg-white text-yellow-700',
  FAILED:         'border-red-300    bg-white text-red-700',
  DEGRADED:       'border-purple-300 bg-white text-purple-700',
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
  onExportReport?: (format: 'markdown' | 'json') => void;
  /** Override "now" for deterministic tests. */
  now?: Date;
}

export default function PlanRunHero({
  run,
  planName,
  isAborting = false,
  onAbort,
  onExportReport,
  now,
}: Props) {
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [exportOpen, setExportOpen] = useState(false);
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
    const end = run.ended_at
      ? new Date(run.ended_at).getTime()
      : (now ?? new Date()).getTime();
    return formatDuration(Math.max(0, (end - start) / 1000));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [run, now, tick]);

  const pill = run ? PLAN_RUN_PILL[run.status] : null;
  const heroCls  = run ? HERO_CLS[run.status]  : 'border-gray-200 bg-white';
  const badgeCls = run ? BADGE_CLS[run.status] : '';
  const isRunning = run?.status === 'RUNNING';

  return (
    <div className={`rounded-xl border shadow-sm overflow-hidden ${heroCls}`}>
      <div className="px-4 pt-3 pb-1">
        {/* Plan 标识 */}
        <div className="text-[11px] text-gray-400 mb-0.5">
          <span className="font-semibold text-blue-600">
            {planName ? `Plan #${run?.plan_id} · ${planName}` : `Plan #${run?.plan_id ?? '—'}`}
          </span>
        </div>
        <div className="text-sm font-bold text-gray-900">
          PlanRun{' '}
          <span className={isRunning ? 'text-orange-600' : 'text-gray-700'}>
            #{run?.id ?? '—'}
          </span>
        </div>
      </div>

      {/* 大状态 badge */}
      <div className="px-4 pb-3">
        {pill && run && (
          <div
            data-testid="plan-run-status-pill"
            className={`inline-flex items-center gap-2 rounded-xl border px-3.5 py-2 shadow-sm ${badgeCls}`}
          >
            {isRunning && (
              <span className="relative flex h-2.5 w-2.5 shrink-0">
                <span className="absolute inset-0 rounded-full bg-orange-400 opacity-60 animate-ping" />
                <span className="relative h-2.5 w-2.5 rounded-full bg-orange-500" />
              </span>
            )}
            <pill.Icon
              className={`h-4 w-4 ${isRunning ? 'animate-spin' : ''}`}
            />
            <div>
              <div className="text-sm font-bold">{pill.label}</div>
              {runDuration && (
                <div
                  data-testid="plan-run-duration"
                  className="font-mono text-[11px] opacity-70"
                >
                  {runDuration}
                </div>
              )}
            </div>
          </div>
        )}
      </div>

      {/* 2×2 meta 网格 */}
      <div className="px-4 pb-3 grid grid-cols-2 gap-x-3 gap-y-1 text-[11px]">
        <span className="text-gray-400">触发方式</span>
        <span className="font-medium text-gray-700">{run?.run_type ?? '—'}</span>
        <span className="text-gray-400">操作人</span>
        <span className="font-medium text-gray-700">{run?.triggered_by ?? '—'}</span>
        <span className="text-gray-400">开始时间</span>
        <span className="font-mono text-gray-700">
          {run?.started_at
            ? new Date(run.started_at).toLocaleString('zh-CN', {
                month: '2-digit', day: '2-digit',
                hour: '2-digit', minute: '2-digit',
              })
            : '—'}
        </span>
        <span className="text-gray-400">失败阈值</span>
        <span className="font-medium text-gray-700">
          {run?.failure_threshold != null
            ? `${Math.round(run.failure_threshold * 100)}%`
            : '—'}
        </span>
      </div>

      {/* 操作按钮行 */}
      <div className="flex gap-1.5 px-4 pb-4">
        <div className="relative flex-1">
          <Button
            variant="outline"
            size="sm"
            data-testid="plan-run-export-btn"
            onClick={() => setExportOpen((v) => !v)}
            disabled={!run}
            className="w-full text-[11px] h-7"
          >
            <Download className="mr-1 h-3 w-3" />
            导出报告
            <ChevronDown className="ml-1 h-3 w-3" />
          </Button>
          {exportOpen && run && (
            <>
              <div className="fixed inset-0 z-10" onClick={() => setExportOpen(false)} />
              <div className="absolute left-0 right-0 top-full z-20 mt-1 overflow-hidden rounded-md border bg-white shadow-lg">
                <button
                  type="button"
                  data-testid="plan-run-export-md"
                  onClick={() => {
                    setExportOpen(false);
                    onExportReport?.('markdown');
                  }}
                  className="block w-full px-3 py-1.5 text-left text-[11px] hover:bg-gray-50"
                >
                  Markdown (.md)
                </button>
                <button
                  type="button"
                  data-testid="plan-run-export-json"
                  onClick={() => {
                    setExportOpen(false);
                    onExportReport?.('json');
                  }}
                  className="block w-full px-3 py-1.5 text-left text-[11px] hover:bg-gray-50"
                >
                  JSON (.json)
                </button>
              </div>
            </>
          )}
        </div>

        {!isTerminal && (
          <Button
            variant="destructive"
            size="sm"
            data-testid="plan-run-abort-btn"
            onClick={() => setConfirmOpen(true)}
            disabled={!run || isAborting}
            className="flex-1 text-[11px] h-7"
          >
            {isAborting ? (
              <><Loader2 className="mr-1 h-3 w-3 animate-spin" />中止中…</>
            ) : (
              <><X className="mr-1 h-3 w-3" />中止运行</>
            )}
          </Button>
        )}
      </div>

      <AlertDialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>确认中止 PlanRun?</AlertDialogTitle>
            <AlertDialogDescription>
              将释放运行中设备的租约，PENDING Job 标记为 ABORTED；Agent 上正在运行的 step
              会异步收到中止信号。操作不可撤销。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <div className="space-y-2">
            <label className="block text-sm font-medium text-gray-700">中止原因（可选）</label>
            <input
              type="text"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="例如：资源池整改"
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
    </div>
  );
}
