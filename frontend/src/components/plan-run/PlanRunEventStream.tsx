import { useState } from 'react';
import { AlertCircle, ChevronLeft, ChevronRight } from 'lucide-react';
import { Skeleton } from '@/components/ui/skeleton';
import {
  EVENT_SEVERITY_DOT,
  EVENT_STAGE_CHIP,
  FILTER_CHIP,
  INTERACTIVE,
  PANEL,
  TEXT,
} from '@/design-system';
import { cn } from '@/lib/utils';
import type {
  EventSeverity,
  EventStage,
  PlanRunEvent,
  PlanRunEventsPayload,
} from '@/utils/api/types';

const SEVERITY_CLS: Record<EventSeverity, { dot: string; node: string }> = {
  ok: { dot: EVENT_SEVERITY_DOT.ok, node: EVENT_SEVERITY_DOT.ok },
  info: { dot: EVENT_SEVERITY_DOT.info, node: EVENT_SEVERITY_DOT.info },
  warn: { dot: EVENT_SEVERITY_DOT.warn, node: EVENT_SEVERITY_DOT.warn },
  err: { dot: EVENT_SEVERITY_DOT.err, node: EVENT_SEVERITY_DOT.err },
};

const STAGE_CHIP_CLS: Record<EventStage, string> = EVENT_STAGE_CHIP;

const STAGE_CHIP_LABEL: Record<EventStage, string> = {
  trigger: '触发',
  init: 'INIT',
  patrol: 'PATROL',
  teardown: 'TEARDOWN',
  system: '系统',
};

const STAGE_FILTERS: Array<{ key: EventStage | 'all'; label: string }> = [
  { key: 'all', label: '全部' },
  { key: 'trigger', label: '触发' },
  { key: 'init', label: 'INIT' },
  { key: 'patrol', label: 'PATROL' },
  { key: 'teardown', label: 'TEARDOWN' },
  { key: 'system', label: '系统' },
];

const SEVERITY_FILTERS: Array<{ key: EventSeverity | 'all'; label: string }> = [
  { key: 'all', label: '全部' },
  { key: 'err', label: '异常' },
  { key: 'warn', label: '告警' },
  { key: 'info', label: '信息' },
  { key: 'ok', label: '完成' },
];

function fmtTs(ts: string): string {
  if (!ts) return '';
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return ts;
  return d.toLocaleString('zh-CN', { hour12: false });
}

function EventRow({ event }: { event: PlanRunEvent }) {
  const [expanded, setExpanded] = useState(false);
  const sevCfg = SEVERITY_CLS[event.severity];
  return (
    <div
      data-testid={`event-row-${event.ts}-${event.category}`}
      className="grid grid-cols-[140px_16px_1fr_auto] items-start gap-2 border-b border-border/40 px-3 py-2.5 text-xs last:border-b-0 hover:bg-muted/30"
    >
      <span className="pt-0.5 font-mono text-[11px] tabular-nums text-muted-foreground/70">
        {fmtTs(event.ts)}
      </span>
      <div className="relative flex justify-center pt-1.5">
        <span className={cn('z-10 h-2 w-2 rounded-full shadow-[0_0_0_3px_hsl(var(--card))]', sevCfg.node)} />
      </div>
      <div className="min-w-0">
        <div className={cn('truncate font-semibold', TEXT.heading)}>{event.title}</div>
        {event.description && (
          <div
            data-testid={`event-desc-${event.ts}-${event.category}`}
            onClick={() => setExpanded((v) => !v)}
            title={expanded ? '点击收起' : '点击展开'}
            className={cn(
              'mt-0.5 cursor-pointer text-xs leading-snug hover:text-foreground',
              TEXT.subtitle,
              expanded ? 'whitespace-pre-wrap break-words' : 'line-clamp-2',
            )}
          >
            {event.description}
          </div>
        )}
        {(event.device_serial || event.job_id) && (
          <div className="mt-0.5 text-[11px] text-muted-foreground/70">
            {event.device_serial && <span className="font-mono">{event.device_serial}</span>}
            {event.job_id && <span className="ml-1">· Job #{event.job_id}</span>}
          </div>
        )}
      </div>
      <span className={cn('shrink-0 rounded border px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wider', STAGE_CHIP_CLS[event.stage])}>
        {STAGE_CHIP_LABEL[event.stage]}
      </span>
    </div>
  );
}

interface Props {
  events: PlanRunEventsPayload | undefined;
  stageFilter?: EventStage | 'all';
  severityFilter?: EventSeverity | 'all';
  onStageFilterChange?: (s: EventStage | 'all') => void;
  onSeverityFilterChange?: (s: EventSeverity | 'all') => void;
  isLoading?: boolean;
  isError?: boolean;
  page?: number;
  pageSize?: number;
  onPageChange?: (page: number) => void;
}

export default function PlanRunEventStream({
  events,
  stageFilter = 'all',
  severityFilter = 'all',
  onStageFilterChange,
  onSeverityFilterChange,
  isLoading = false,
  isError = false,
  page = 0,
  pageSize = 50,
  onPageChange,
}: Props) {
  const eventList = events?.events ?? [];
  const total = events?.total ?? 0;
  const facetStage = events?.facets?.by_stage ?? {};
  const facetSev = events?.facets?.by_severity ?? {};

  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  const from = total === 0 ? 0 : page * pageSize + 1;
  const to = Math.min(total, (page + 1) * pageSize);

  return (
    <div data-testid="plan-run-event-stream" className={PANEL.root}>
      <div className="flex flex-wrap items-center gap-1 border-b bg-card px-3 py-2">
        <span className={cn('mr-1 text-[11px] font-bold uppercase tracking-wider', TEXT.subtitle)}>阶段</span>
        {STAGE_FILTERS.map((f) => (
          <button
            key={f.key}
            type="button"
            data-testid={`event-filter-stage-${f.key}`}
            onClick={() => onStageFilterChange?.(f.key)}
            className={cn(
              'rounded-md px-2 py-0.5 text-xs transition',
              stageFilter === f.key ? FILTER_CHIP.active : FILTER_CHIP.idle,
            )}
          >
            {f.label}
            <span className={cn('ml-1', FILTER_CHIP.count)}>{facetStage[f.key] ?? 0}</span>
          </button>
        ))}
        <span className={FILTER_CHIP.divider} />
        <span className={cn('mr-1 text-[11px] font-bold uppercase tracking-wider', TEXT.subtitle)}>严重度</span>
        {SEVERITY_FILTERS.map((f) => (
          <button
            key={f.key}
            type="button"
            data-testid={`event-filter-sev-${f.key}`}
            onClick={() => onSeverityFilterChange?.(f.key)}
            className={cn(
              'inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-xs transition',
              severityFilter === f.key ? FILTER_CHIP.active : FILTER_CHIP.idle,
            )}
          >
            {f.key !== 'all' && (
              <span className={cn('h-1.5 w-1.5 rounded-full', SEVERITY_CLS[f.key as EventSeverity]?.dot ?? 'bg-muted-foreground/40')} />
            )}
            {f.label}
            <span className={FILTER_CHIP.count}>{facetSev[f.key] ?? 0}</span>
          </button>
        ))}
      </div>

      <div data-testid="event-list">
        {isError ? (
          <div className="flex flex-col items-center justify-center py-10 text-center">
            <AlertCircle className="mb-1 h-5 w-5 text-destructive/60" />
            <span className="text-xs font-semibold text-destructive">加载失败</span>
            <span className="mt-0.5 text-[11px] text-destructive/70">请检查网络连接或稍后重试</span>
          </div>
        ) : isLoading && eventList.length === 0 ? (
          <div className="space-y-1.5 p-3">
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-full" />
          </div>
        ) : eventList.length === 0 ? (
          <div className={cn('flex flex-col items-center justify-center py-10 text-xs', TEXT.subtitle)}>
            该过滤条件下暂无事件
            <span className="mt-1 text-[11px] text-muted-foreground/60">尝试切换阶段或严重度</span>
          </div>
        ) : (
          <div className="flex flex-col">
            {eventList.map((e, idx) => (
              <EventRow key={`${e.ts}-${e.category}-${idx}`} event={e} />
            ))}
          </div>
        )}
      </div>

      {total > 0 && (
        <div
          data-testid="event-pagination"
          className={cn('flex items-center justify-between border-t bg-muted/50 px-3 py-2 text-xs', TEXT.subtitle)}
        >
          <span>
            第 <b className={cn('font-mono', TEXT.body)}>{from}-{to}</b> / 共{' '}
            <b className={cn('font-mono', TEXT.body)}>{total}</b> 条
          </span>
          <div className="flex items-center gap-1">
            <button
              type="button"
              data-testid="event-page-prev"
              disabled={page <= 0}
              onClick={() => onPageChange?.(page - 1)}
              className={cn('inline-flex items-center gap-0.5 rounded border px-2 py-0.5 transition disabled:opacity-40', INTERACTIVE.hover)}
            >
              <ChevronLeft className="h-3 w-3" />
              上一页
            </button>
            <span className="px-1 font-mono">{page + 1}/{pageCount}</span>
            <button
              type="button"
              data-testid="event-page-next"
              disabled={page + 1 >= pageCount}
              onClick={() => onPageChange?.(page + 1)}
              className={cn('inline-flex items-center gap-0.5 rounded border px-2 py-0.5 transition disabled:opacity-40', INTERACTIVE.hover)}
            >
              下一页
              <ChevronRight className="h-3 w-3" />
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
