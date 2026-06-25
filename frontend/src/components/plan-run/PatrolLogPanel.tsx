import { useState, useMemo } from 'react';
import { ChevronDown, ChevronRight, AlertTriangle, Info, XCircle } from 'lucide-react';
import {
  FILTER_CHIP,
  INTERACTIVE,
  PATROL_EVENT_SEVERITY,
  STATUS_CHIP,
  TEXT,
} from '@/design-system';
import { cn } from '@/lib/utils';
import { formatTimeLabel } from '@/utils/format';
import type { PlanRunEventsPayload, PlanRunTimeline, PlanRunEvent, TimelineStage } from '@/utils/api/types';
import SectionHeader from './SectionHeader';

interface Props {
  events?: PlanRunEventsPayload;
  timeline?: PlanRunTimeline;
  isLoading?: boolean;
  isError?: boolean;
  page?: number;
  pageSize?: number;
  onPageChange?: (page: number) => void;
  onSeverityChange?: (severity: string) => void;
  onDeviceChange?: (serial: string) => void;
  severityFilter?: string;
  deviceFilter?: string;
}

const SEVERITY_ICON: Record<string, JSX.Element> = {
  err: <XCircle className="h-3.5 w-3.5 text-destructive" />,
  warn: <AlertTriangle className="h-3.5 w-3.5 text-warning" />,
  info: <Info className="h-3.5 w-3.5 text-info" />,
  ok: <Info className="h-3.5 w-3.5 text-success" />,
};

function inferCycle(
  ts: string,
  patrolStage: TimelineStage | undefined,
  intervalSeconds: number | undefined,
): number {
  if (!patrolStage?.started_at || !intervalSeconds || intervalSeconds <= 0) return 0;
  const stageStart = new Date(patrolStage.started_at).getTime();
  const elapsed = (new Date(ts).getTime() - stageStart) / 1000;
  return Math.max(0, Math.floor(elapsed / intervalSeconds));
}

function groupByCycle(
  events: PlanRunEvent[],
  patrolStage: TimelineStage | undefined,
  intervalSeconds: number | undefined,
): Map<number, PlanRunEvent[]> {
  const map = new Map<number, PlanRunEvent[]>();
  for (const ev of events) {
    const cycle =
      patrolStage?.patrol_cycle_index != null
        ? patrolStage.patrol_cycle_index
        : inferCycle(ev.ts, patrolStage, intervalSeconds);
    if (!map.has(cycle)) map.set(cycle, []);
    map.get(cycle)!.push(ev);
  }
  return map;
}

function EventRow({ ev }: { ev: PlanRunEvent }) {
  const severity = ev.severity ?? 'info';
  const cls = PATROL_EVENT_SEVERITY[severity as keyof typeof PATROL_EVENT_SEVERITY] ?? PATROL_EVENT_SEVERITY.info;
  const icon = SEVERITY_ICON[severity] ?? SEVERITY_ICON.info;

  return (
    <div className={cn('flex gap-2 rounded border px-2.5 py-1.5 text-xs', cls)}>
      <span className="mt-0.5 shrink-0">{icon}</span>
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline justify-between gap-2">
          <span className="truncate font-medium">{ev.title}</span>
          {ev.device_serial && (
            <span className="shrink-0 font-mono text-[10px] opacity-60">{ev.device_serial}</span>
          )}
        </div>
        {ev.description && (
          <div className="mt-0.5 text-[10px] opacity-70">{ev.description}</div>
        )}
        {ev.ts && (
          <div className="mt-0.5 font-mono text-[10px] opacity-50">
            {formatTimeLabel(ev.ts)}
          </div>
        )}
      </div>
    </div>
  );
}

function CycleAccordion({ cycle, events }: { cycle: number; events: PlanRunEvent[] }) {
  const [open, setOpen] = useState(true);
  const errorCount = events.filter((e) => e.severity === 'err').length;

  return (
    <div className="overflow-hidden rounded-lg border">
      <button
        className={cn('flex w-full items-center gap-2 bg-muted/50 px-3 py-2 text-left', INTERACTIVE.hover)}
        onClick={() => setOpen((o) => !o)}
        data-testid={`cycle-accordion-${cycle}`}
      >
        {open
          ? <ChevronDown className={cn('h-3.5 w-3.5', TEXT.subtitle)} />
          : <ChevronRight className={cn('h-3.5 w-3.5', TEXT.subtitle)} />}
        <span className={cn('text-xs font-semibold', TEXT.body)}>巡检周期 #{cycle + 1}</span>
        <span className={cn('text-[10px]', TEXT.subtitle)}>{events.length} 条</span>
        {errorCount > 0 && (
          <span className={cn('ml-auto rounded-full px-1.5 py-0.5 text-[10px]', STATUS_CHIP.destructive)}>
            {errorCount} 错误
          </span>
        )}
      </button>
      {open && (
        <div className="space-y-1 p-2">
          {events.map((ev, i) => (
            <EventRow key={ev.job_id != null ? `${ev.job_id}-${i}` : i} ev={ev} />
          ))}
        </div>
      )}
    </div>
  );
}

const SEVERITIES = ['ALL', 'ok', 'info', 'warn', 'err'];
const SEVERITY_LABELS: Record<string, string> = {
  ALL: 'ALL',
  ok: 'OK',
  info: 'INFO',
  warn: 'WARN',
  err: 'ERR',
};

export default function PatrolLogPanel({
  events,
  timeline,
  isLoading = false,
  isError = false,
  page = 1,
  pageSize = 50,
  onPageChange,
  onSeverityChange,
  onDeviceChange,
  severityFilter = 'ALL',
  deviceFilter = '',
}: Props) {
  const rawEvents = useMemo<PlanRunEvent[]>(() => events?.events ?? [], [events]);
  const totalCount = events?.total;

  const patrolStage = useMemo(
    () => timeline?.stages?.find((s) => s.stage === 'patrol'),
    [timeline],
  );

  const intervalSeconds = useMemo(
    () => patrolStage?.patrol_interval_seconds ?? undefined,
    [patrolStage],
  );

  const cycleMap = useMemo(
    () => groupByCycle(rawEvents, patrolStage, intervalSeconds),
    [rawEvents, patrolStage, intervalSeconds],
  );

  const cycles = useMemo(() => Array.from(cycleMap.keys()).sort((a, b) => a - b), [cycleMap]);
  const totalPages = totalCount != null ? Math.ceil(totalCount / pageSize) : null;

  return (
    <div className="space-y-3" data-testid="patrol-log-panel">
      <SectionHeader title="巡检日志" color="amber" />

      <div className="flex flex-wrap gap-2">
        <div className="flex gap-1">
          {SEVERITIES.map((s) => (
            <button
              key={s}
              onClick={() => onSeverityChange?.(s)}
              className={cn(
                'rounded px-2 py-0.5 text-[10px] font-medium',
                severityFilter === s
                  ? 'bg-primary text-primary-foreground'
                  : cn('bg-muted', TEXT.subtitle, INTERACTIVE.hover),
              )}
              data-testid={`severity-btn-${s}`}
            >
              {SEVERITY_LABELS[s] ?? s}
            </button>
          ))}
        </div>
        <input
          type="text"
          placeholder="过滤设备 serial"
          value={deviceFilter}
          onChange={(e) => onDeviceChange?.(e.target.value)}
          className="min-w-[120px] rounded border bg-card px-2 py-0.5 text-[10px] focus:outline-none focus:ring-2 focus:ring-primary/20"
          data-testid="device-filter-input"
        />
      </div>

      {isLoading && (
        <div className={cn('flex h-20 items-center justify-center text-xs', TEXT.subtitle)}>加载中…</div>
      )}
      {isError && (
        <div className="flex h-20 items-center justify-center text-xs text-destructive">加载失败</div>
      )}

      {!isLoading && !isError && (
        <>
          {cycles.length === 0 && (
            <div className={cn('flex h-16 items-center justify-center text-xs', TEXT.subtitle)}>
              暂无巡检日志
            </div>
          )}
          <div className="space-y-2">
            {cycles.map((cycle) => (
              <CycleAccordion
                key={cycle}
                cycle={cycle}
                events={cycleMap.get(cycle) ?? []}
              />
            ))}
          </div>

          {totalPages != null && totalPages > 1 && (
            <div className={cn('flex items-center justify-center gap-2')}>
              <button
                disabled={page <= 1}
                onClick={() => onPageChange?.(page - 1)}
                className={cn('rounded px-2 py-1 text-xs disabled:opacity-40', FILTER_CHIP.idle, INTERACTIVE.hover)}
                data-testid="patrol-prev-page"
              >
                上一页
              </button>
              <span className={cn('text-[10px]', TEXT.subtitle)}>
                {page} / {totalPages}
              </span>
              <button
                disabled={page >= totalPages}
                onClick={() => onPageChange?.(page + 1)}
                className={cn('rounded px-2 py-1 text-xs disabled:opacity-40', FILTER_CHIP.idle, INTERACTIVE.hover)}
                data-testid="patrol-next-page"
              >
                下一页
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
}
