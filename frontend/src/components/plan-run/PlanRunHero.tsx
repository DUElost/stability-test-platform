import { useEffect, useMemo, useRef, useState } from 'react';
import { Download, X, Loader2, ChevronDown, RotateCcw } from 'lucide-react';
import { Button, buttonVariants } from '@/components/ui/button';
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
import {
  PLAN_RUN_HERO_BADGE,
  PLAN_RUN_HERO_SURFACE,
  type PlanRunHeroStatus,
} from '@/design-system/colors';
import { ELEVATION, INTERACTIVE, SURFACE, TEXT } from '@/design-system/tokens';
import { cn } from '@/lib/utils';
import { formatDateTimeShort, formatDurationSeconds } from '@/utils/format';
import { ProjectKeyBadge } from '@/components/project/ProjectFilterSelect';
import { PLAN_RUN_PILL, isPlanRunTerminal } from './planRunStatus';

// 状态 → 容器背景/边框（与 StatusBadge plan-run 语义对齐）
const HERO_CLS: Record<PlanRunStatus, string> = PLAN_RUN_HERO_SURFACE;

// 状态 → badge 样式
const BADGE_CLS: Record<PlanRunStatus, string> = PLAN_RUN_HERO_BADGE;

interface Props {
  run: PlanRun | undefined;
  planName?: string | null;
  isAborting?: boolean;
  onAbort?: (reason: string) => void;
  onExportReport?: (format: 'markdown' | 'json') => void;
  /** 复跑：沿用本次 Plan 与设备集重新发起（仅终态显示）。 */
  onRerun?: () => void;
  /** Override "now" for deterministic tests. */
  now?: Date;
}

export interface PlanRunHeroActionsProps {
  run: PlanRun | undefined;
  isAborting?: boolean;
  onAbort?: (reason: string) => void;
  onExportReport?: (format: 'markdown' | 'json') => void;
  onRerun?: () => void;
  className?: string;
}

/** 导出 / 复跑 / 中止 — Hero 卡片内操作条。 */
export function PlanRunHeroActions({
  run,
  isAborting = false,
  onAbort,
  onExportReport,
  onRerun,
  className,
}: PlanRunHeroActionsProps) {
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [exportOpen, setExportOpen] = useState(false);
  const [reason, setReason] = useState('');
  const exportBtnRef = useRef<HTMLButtonElement | null>(null);
  const [exportPos, setExportPos] = useState<{
    top: number;
    left: number;
    width: number;
    openUp: boolean;
  } | null>(null);

  const isTerminal = !!run && isPlanRunTerminal(run.status);
  const canAbort = run?.capabilities
    ? run.capabilities.abort === true
    : !isTerminal;

  const openExportMenu = () => {
    const rect = exportBtnRef.current?.getBoundingClientRect();
    if (rect) {
      const menuH = 72;
      const spaceBelow = window.innerHeight - rect.bottom;
      const openUp = spaceBelow < menuH + 12;
      setExportPos({
        top: openUp ? rect.top : rect.bottom,
        left: rect.left,
        width: rect.width,
        openUp,
      });
    }
    setExportOpen((v) => !v);
  };

  return (
    <div className={cn('flex gap-1.5', className)}>
      <div className="relative flex-1">
        <Button
          variant="outline"
          size="sm"
          ref={exportBtnRef}
          data-testid="plan-run-export-btn"
          onClick={openExportMenu}
          disabled={!run}
          className="w-full text-[11px] h-7"
        >
          <Download className="mr-1 h-3 w-3" />
          导出报告
          <ChevronDown className="ml-1 h-3 w-3" />
        </Button>
        {exportOpen && run && (
          <>
            <div className="fixed inset-0 z-20" onClick={() => setExportOpen(false)} />
            <div
              className={cn(
                'fixed z-30 overflow-hidden rounded-md border shadow-lg',
                SURFACE.elevated,
                ELEVATION.dropdown,
              )}
              style={{
                top: exportPos?.openUp
                  ? undefined
                  : (exportPos?.top ?? 0) + 4,
                bottom: exportPos?.openUp
                  ? window.innerHeight - (exportPos?.top ?? 0) + 4
                  : undefined,
                left: exportPos?.left ?? 0,
                width: exportPos?.width ?? 160,
              }}
            >
              <button
                type="button"
                data-testid="plan-run-export-md"
                onClick={() => {
                  setExportOpen(false);
                  onExportReport?.('markdown');
                }}
                className={cn('block w-full px-3 py-1.5 text-left text-[11px]', INTERACTIVE.menuItem)}
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
                className={cn('block w-full px-3 py-1.5 text-left text-[11px]', INTERACTIVE.menuItem)}
              >
                JSON (.json)
              </button>
            </div>
          </>
        )}
      </div>

      {isTerminal && onRerun && (
        <Button
          variant="outline"
          size="sm"
          data-testid="plan-run-rerun-btn"
          onClick={onRerun}
          disabled={!run}
          className="flex-1 text-[11px] h-7"
        >
          <RotateCcw className="mr-1 h-3 w-3" />
          复跑
        </Button>
      )}

      {canAbort && (
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

      <AlertDialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>确认中止 PlanRun?</AlertDialogTitle>
            <AlertDialogDescription>
              PENDING Job 将直接标记为 ABORTED；运行中 Job 会收到中止请求，并在 Agent
              确认执行进程停止后释放租约。操作不可撤销。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <div className="space-y-2">
            <label htmlFor="pr-abort-reason" className={cn('block text-sm font-medium', TEXT.heading)}>中止原因（可选）</label>
            <input
              id="pr-abort-reason"
              type="text"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="例如：资源池整改"
              className="w-full rounded-md border border-border px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-destructive/30"
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
              className={cn(buttonVariants({ variant: 'destructive' }))}
            >
              确认中止
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

export default function PlanRunHero({
  run,
  planName,
  isAborting = false,
  onAbort,
  onExportReport,
  onRerun,
  now,
}: Props) {
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
    return formatDurationSeconds(Math.max(0, (end - start) / 1000));
    // tick 不在函数体内使用，是刻意多加的依赖：运行中 now 为空时靠每秒自增的
    // tick 驱动重算，否则时长会冻在首次渲染的值上（#260 待统一 tick 状态）
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [run, now, tick]);

  const pill = run ? PLAN_RUN_PILL[run.status] : null;
  const heroCls = run ? HERO_CLS[run.status as PlanRunHeroStatus] : cn(SURFACE.elevated, 'border-border');
  const badgeCls = run ? BADGE_CLS[run.status as PlanRunHeroStatus] : '';
  const isRunning = run?.status === 'RUNNING';
  const isQueuedPhase = run?.status === 'QUEUED' || run?.status === 'PRECHECK';

  // ADR-0026: queue wait is measured from enqueued_at (never started_at —
  // admission resets started_at to the real execution start).
  const queueWait = useMemo(() => {
    if (!run?.enqueued_at || !isQueuedPhase) return null;
    const start = new Date(run.enqueued_at).getTime();
    const end = (now ?? new Date()).getTime();
    return formatDurationSeconds(Math.max(0, (end - start) / 1000));
    // 同上：tick 为刻意多加的每秒驱动依赖，排队等待时长需随时间自增
    // （#260 待统一 tick 状态）
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [run, now, tick, isQueuedPhase]);

  const queueBlockers = isQueuedPhase
    ? ((run?.run_context as Record<string, unknown> | null | undefined)?.[
        'queue_blockers'
      ] as Array<{ id?: number; reason?: string }> | undefined) ?? []
    : [];

  const QUEUE_REASON_LABEL: Record<string, string> = {
    DEVICE_BUSY: '设备占用中',
    RESOURCE_BUSY: '资源不足',
    PRIORITY_WAIT: '等待优先级调度',
    PRECHECK_STALE: '准入中断,等待重试',
  };

  return (
    <div className={cn('rounded-lg border overflow-hidden', ELEVATION.flat, heroCls)}>
      {/* 左：PlanRun / #id 两行；右：状态块对齐增高，避免挤成小 pill */}
      <div className="px-4 pt-4 pb-3">
        <div className="flex items-stretch justify-between gap-3">
          <div className="min-w-0">
            <div className={cn('text-xs font-medium uppercase tracking-wide', TEXT.subtitle)}>
              PlanRun
            </div>
            <h2
              className={cn(
                'mt-0.5 text-xl font-semibold leading-7 tabular-nums',
                TEXT.heading,
                isRunning && 'text-primary',
              )}
            >
              #{run?.id ?? '—'}
            </h2>
          </div>
          {pill && run && (
            <div
              data-testid="plan-run-status-pill"
              className={`flex min-w-[5.5rem] shrink-0 flex-col justify-center gap-0.5 rounded-md border px-3 py-2 shadow-none ${badgeCls}`}
            >
              <div className="flex items-center gap-1.5">
                {isRunning && (
                  <span className="relative flex h-2 w-2 shrink-0">
                    <span className="absolute inset-0 rounded-full bg-warning/60 opacity-60 animate-ping" />
                    <span className="relative h-2 w-2 rounded-full bg-warning" />
                  </span>
                )}
                <pill.Icon className={`h-3.5 w-3.5 shrink-0 ${isRunning ? 'animate-spin' : ''}`} />
                <span className="text-sm font-semibold leading-none">{pill.label}</span>
              </div>
              {runDuration && (
                <div
                  data-testid="plan-run-duration"
                  className="pl-5 font-mono text-xs tabular-nums opacity-80"
                >
                  {runDuration}
                </div>
              )}
            </div>
          )}
        </div>
        <div className={cn('mt-1.5 flex min-w-0 flex-wrap items-center gap-1.5 text-xs', TEXT.subtitle)}>
          <span className="min-w-0 break-words" title={planName ?? undefined}>
            {planName
              ? `Plan #${run?.plan_id} · ${planName}`
              : `Plan #${run?.plan_id ?? '—'}`}
          </span>
          <ProjectKeyBadge projectKey={run?.project_key} />
        </div>
      </div>

      {/* 2×2 meta 网格 */}
      <div className={cn('px-4 pb-4 grid grid-cols-2 gap-x-3 gap-y-2 text-xs', TEXT.caption)}>
        <span>触发方式</span>
        <span className={cn('font-medium', TEXT.heading)}>{run?.run_type ?? '—'}</span>
        <span>操作人</span>
        <span className={cn('font-medium', TEXT.heading)}>{run?.triggered_by ?? '—'}</span>
        <span>开始时间</span>
        <span className={cn('font-mono', TEXT.heading)}>
          {formatDateTimeShort(run?.started_at)}
        </span>
        <span>失败阈值</span>
        <span className={cn('font-medium', TEXT.heading)}>
          {run?.failure_threshold != null
            ? `${Math.round(run.failure_threshold * 100)}%`
            : '—'}
        </span>
        {typeof run?.run_context?.note === 'string' && run.run_context.note.trim() ? (
          <>
            <span>执行备注</span>
            <span className={cn('font-medium break-words', TEXT.heading)} title={run.run_context.note}>
              {run.run_context.note}
            </span>
          </>
        ) : null}
      </div>

      {/* ADR-0026: 准入队列信息条(仅 QUEUED/PRECHECK) */}
      {isQueuedPhase && run && (
        <div
          data-testid="plan-run-queue-info"
          className={cn(
            'mx-4 mb-3 rounded-lg border border-border bg-muted/30 px-3 py-2',
            'grid grid-cols-2 gap-x-3 gap-y-1 text-[11px]',
            TEXT.caption,
          )}
        >
          <span>排队原因</span>
          <span className={cn('font-medium', TEXT.heading)}>
            {run.queue_reason
              ? QUEUE_REASON_LABEL[run.queue_reason] ?? run.queue_reason
              : run.status === 'PRECHECK'
                ? '准入检查中'
                : '等待调度'}
          </span>
          <span>已排队</span>
          <span className={cn('font-mono', TEXT.heading)}>{queueWait ?? '—'}</span>
          <span>下次准入</span>
          <span className={cn('font-mono', TEXT.heading)}>
            {run.next_admission_at ? formatDateTimeShort(run.next_admission_at) : '就绪即准入'}
          </span>
          <span>阻塞设备</span>
          <span className={cn('font-medium', TEXT.heading)}>
            {queueBlockers.length > 0
              ? `${queueBlockers.length} 台(${[
                  ...new Set(queueBlockers.map((b) => b.reason ?? 'unknown')),
                ].join(', ')})`
              : '—'}
          </span>
        </div>
      )}

      <PlanRunHeroActions
        run={run}
        isAborting={isAborting}
        onAbort={onAbort}
        onExportReport={onExportReport}
        onRerun={onRerun}
        className="px-4 pb-4"
      />
    </div>
  );
}
