import { useState } from 'react';
import { AlertCircle, ChevronLeft, ChevronRight } from 'lucide-react';
import { Skeleton } from '@/components/ui/skeleton';
import type {
  EventSeverity,
  EventStage,
  PlanRunEvent,
  PlanRunEventsPayload,
} from '@/utils/api/types';

const SEVERITY_CLS: Record<EventSeverity, { dot: string; node: string }> = {
  ok: { dot: 'bg-green-500', node: 'bg-green-500' },
  info: { dot: 'bg-blue-500', node: 'bg-blue-500' },
  warn: { dot: 'bg-amber-500', node: 'bg-amber-500' },
  err: { dot: 'bg-red-500', node: 'bg-red-500' },
};

const STAGE_CHIP_CLS: Record<EventStage, string> = {
  trigger: 'bg-purple-50 text-purple-700',
  init: 'bg-blue-50 text-blue-700',
  patrol: 'bg-orange-50 text-orange-700',
  teardown: 'bg-gray-50 text-gray-600',
  system: 'bg-slate-50 text-slate-600',
};

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

// Logs span many patrol cycles → show full date-time, not just time-of-day.
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
      className="grid grid-cols-[140px_16px_1fr_auto] items-start gap-2 border-b border-gray-50 px-3 py-2.5 text-xs last:border-b-0 hover:bg-gray-50/50"
    >
      <span className="pt-0.5 font-mono text-[11px] tabular-nums text-gray-400">
        {fmtTs(event.ts)}
      </span>
      <div className="relative flex justify-center pt-1.5">
        <span className={`z-10 h-2 w-2 rounded-full ${sevCfg.node} shadow-[0_0_0_3px_#fff]`} />
      </div>
      <div className="min-w-0">
        <div className="truncate font-semibold text-gray-900">{event.title}</div>
        {event.description && (
          <div
            data-testid={`event-desc-${event.ts}-${event.category}`}
            onClick={() => setExpanded((v) => !v)}
            title={expanded ? '点击收起' : '点击展开'}
            className={`mt-0.5 cursor-pointer text-xs leading-snug text-gray-500 hover:text-gray-700 ${
              expanded ? 'whitespace-pre-wrap break-words' : 'line-clamp-2'
            }`}
          >
            {event.description}
          </div>
        )}
        {(event.device_serial || event.job_id) && (
          <div className="mt-0.5 text-[11px] text-gray-400">
            {event.device_serial && <span className="font-mono">{event.device_serial}</span>}
            {event.job_id && <span className="ml-1">· Job #{event.job_id}</span>}
          </div>
        )}
      </div>
      <span
        className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wider border ${STAGE_CHIP_CLS[event.stage]}`}
      >
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
  /** 0-based page index. */
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
    <div
      data-testid="plan-run-event-stream"
      className="overflow-hidden rounded-xl border bg-white shadow-sm"
    >
      {/* Filter bar */}
      <div className="flex flex-wrap items-center gap-1 border-b bg-white px-3 py-2">
        <span className="mr-1 text-[11px] font-bold uppercase tracking-wider text-gray-400">阶段</span>
        {STAGE_FILTERS.map((f) => (
          <button
            key={f.key}
            type="button"
            data-testid={`event-filter-stage-${f.key}`}
            onClick={() => onStageFilterChange?.(f.key)}
            className={`rounded-md px-2 py-0.5 text-xs transition ${stageFilter === f.key ? 'bg-blue-100 font-semibold text-blue-700' : 'text-gray-600 hover:bg-gray-100'}`}
          >
            {f.label}
            <span className="ml-1 text-[11px] text-gray-400">{facetStage[f.key] ?? 0}</span>
          </button>
        ))}
        <span className="mx-2 h-3 w-px bg-gray-200" />
        <span className="mr-1 text-[11px] font-bold uppercase tracking-wider text-gray-400">严重度</span>
        {SEVERITY_FILTERS.map((f) => (
          <button
            key={f.key}
            type="button"
            data-testid={`event-filter-sev-${f.key}`}
            onClick={() => onSeverityFilterChange?.(f.key)}
            className={`inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-xs transition ${severityFilter === f.key ? 'bg-blue-100 font-semibold text-blue-700' : 'text-gray-600 hover:bg-gray-100'}`}
          >
            {f.key !== 'all' && (
              <span className={`h-1.5 w-1.5 rounded-full ${SEVERITY_CLS[f.key as EventSeverity]?.dot ?? 'bg-gray-400'}`} />
            )}
            {f.label}
            <span className="text-[11px] text-gray-400">{facetSev[f.key] ?? 0}</span>
          </button>
        ))}
      </div>

      {/* Event list */}
      <div data-testid="event-list">
        {isError ? (
          <div className="flex flex-col items-center justify-center py-10 text-center">
            <AlertCircle className="mb-1 h-5 w-5 text-red-400" />
            <span className="text-xs font-semibold text-red-600">加载失败</span>
            <span className="mt-0.5 text-[11px] text-red-400">请检查网络连接或稍后重试</span>
          </div>
        ) : isLoading && eventList.length === 0 ? (
          <div className="space-y-1.5 p-3">
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-full" />
          </div>
        ) : eventList.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-10 text-xs text-gray-400">
            该过滤条件下暂无事件
            <span className="mt-1 text-[11px] text-gray-300">尝试切换阶段或严重度</span>
          </div>
        ) : (
          <div className="flex flex-col">
            {eventList.map((e, idx) => (
              <EventRow key={`${e.ts}-${e.category}-${idx}`} event={e} />
            ))}
          </div>
        )}
      </div>

      {/* Pagination */}
      {total > 0 && (
        <div
          data-testid="event-pagination"
          className="flex items-center justify-between border-t bg-gray-50 px-3 py-2 text-xs text-gray-500"
        >
          <span>
            第 <b className="font-mono text-gray-700">{from}-{to}</b> / 共{' '}
            <b className="font-mono text-gray-700">{total}</b> 条
          </span>
          <div className="flex items-center gap-1">
            <button
              type="button"
              data-testid="event-page-prev"
              disabled={page <= 0}
              onClick={() => onPageChange?.(page - 1)}
              className="inline-flex items-center gap-0.5 rounded border px-2 py-0.5 transition hover:bg-gray-100 disabled:opacity-40"
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
              className="inline-flex items-center gap-0.5 rounded border px-2 py-0.5 transition hover:bg-gray-100 disabled:opacity-40"
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
