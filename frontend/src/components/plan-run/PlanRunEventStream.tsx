import { useState } from 'react';
import {
  AlertCircle,
  ChevronLeft,
  ChevronRight,
  Download,
  Inbox,
  Search,
  X,
} from 'lucide-react';
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
import { formatDateTimeLocale } from '@/utils/format';
import { loadErrorCopy } from '@/utils/api';
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
  const formatted = formatDateTimeLocale(ts, '');
  return formatted || ts;
}

function EventRow({ event }: { event: PlanRunEvent }) {
  const [expanded, setExpanded] = useState(false);
  const sevCfg = SEVERITY_CLS[event.severity];
  return (
    <div
      data-testid={`event-row-${event.ts}-${event.category}`}
      className="grid grid-cols-[140px_16px_1fr_auto] items-start gap-2 border-b border-border/40 px-3 py-1.5 text-xs last:border-b-0 hover:bg-muted/30"
    >
      {/* 时间戳/设备号保持全量 muted-foreground——透明度变体在白底上低于 WCAG AA */}
      <span className="pt-0.5 font-mono text-[11px] tabular-nums text-muted-foreground">
        {fmtTs(event.ts)}
      </span>
      <div className="relative flex justify-center pt-1.5">
        <span className={cn('z-10 h-2 w-2 rounded-full shadow-[0_0_0_3px_hsl(var(--card))]', sevCfg.node)} />
      </div>
      <div className="min-w-0">
        <div className={cn('truncate font-semibold', TEXT.heading)}>{event.title}</div>
        {event.description && (
          <button
            type="button"
            data-testid={`event-desc-${event.ts}-${event.category}`}
            onClick={() => setExpanded((v) => !v)}
            aria-expanded={expanded}
            title={expanded ? '点击收起' : '点击展开'}
            className={cn(
              'mt-0.5 block w-full cursor-pointer text-left text-xs leading-snug hover:text-foreground',
              TEXT.subtitle,
              expanded ? 'whitespace-pre-wrap break-words' : 'line-clamp-2',
            )}
          >
            {event.description}
          </button>
        )}
        {(event.device_serial || event.job_id) && (
          <div className="mt-0.5 text-[11px] text-muted-foreground">
            {event.device_serial && <span className="font-mono">{event.device_serial}</span>}
            {event.job_id && <span className="ml-1">· Job #{event.job_id}</span>}
          </div>
        )}
      </div>
      <span className={cn('shrink-0 rounded border px-1.5 py-0.5 text-[11px] font-bold uppercase tracking-wider', STAGE_CHIP_CLS[event.stage])}>
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
  /** 搜索关键字（受控值由页面防抖，输入即时上报） */
  search?: string;
  onSearchChange?: (s: string) => void;
  /** 导出当前筛选+搜索结果为 CSV；未提供则不渲染导出按钮 */
  onExportCsv?: () => void;
  isExporting?: boolean;
  isLoading?: boolean;
  isError?: boolean;
  /** 承载错误的原始对象（#2361）：404 与网络层要分文案，只有布尔分不出。 */
  error?: unknown;
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
  search = '',
  onSearchChange,
  onExportCsv,
  isExporting = false,
  isLoading = false,
  isError = false,
  error,
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

  const hasActiveFilters =
    stageFilter !== 'all' || severityFilter !== 'all' || search.trim() !== '';

  const clearAllFilters = () => {
    onStageFilterChange?.('all');
    onSeverityFilterChange?.('all');
    onSearchChange?.('');
  };

  return (
    <div
      data-testid="plan-run-event-stream"
      className={cn(PANEL.root, 'flex h-full min-h-0 flex-col')}
    >
      <div className="flex shrink-0 flex-wrap items-center gap-1 border-b bg-card px-3 py-1.5">
        <span className={cn('mr-1 text-[11px] font-bold uppercase tracking-wider', TEXT.subtitle)}>阶段</span>
        {STAGE_FILTERS.map((f) => (
          <button
            key={f.key}
            type="button"
            data-testid={`event-filter-stage-${f.key}`}
            aria-pressed={stageFilter === f.key}
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
            aria-pressed={severityFilter === f.key}
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
        <div className="relative ml-auto">
          <Search aria-hidden className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
          <input
            type="search"
            data-testid="event-search-input"
            value={search}
            onChange={(e) => onSearchChange?.(e.target.value)}
            placeholder="搜索标题/描述/设备号"
            aria-label="搜索日志事件"
            className="h-7 w-44 rounded-md border border-border bg-background pl-7 pr-6 text-xs text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
          />
          {search !== '' && (
            <button
              type="button"
              data-testid="event-search-clear"
              aria-label="清除搜索"
              onClick={() => onSearchChange?.('')}
              className="absolute right-1.5 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          )}
        </div>
      </div>

      <div
        data-testid="event-list"
        // scrollbar-gutter 稳定占位：滚动条出现/消失不再引起右列横移
        className="min-h-0 flex-1 overflow-y-auto [scrollbar-gutter:stable]"
      >
        {isError ? (
          <div className="flex flex-col items-center justify-center py-10 text-center">
            <AlertCircle aria-hidden className="mb-1 h-5 w-5 text-destructive/60" />
            <span className="text-xs font-semibold text-destructive">加载失败</span>
            {/* #2361：此前不论哪种失败都写「请检查网络连接」——404（记录不存在）
                会被读成网络故障。 */}
            <span className="mt-0.5 text-[11px] text-destructive/70">
              {
                loadErrorCopy(error, {
                  notFound: '执行记录不存在或已被清理，日志无法读取。',
                }).description
              }
            </span>
          </div>
        ) : isLoading && eventList.length === 0 ? (
          <div className="space-y-1.5 p-3">
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-full" />
          </div>
        ) : eventList.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center px-6 py-10 text-center">
            <Inbox aria-hidden className="mb-2 h-8 w-8 text-muted-foreground/40" />
            <span className={cn('text-xs font-medium', TEXT.subtitle)}>该过滤条件下暂无事件</span>
            <span className="mt-1 text-[11px] text-muted-foreground">
              尝试切换阶段、严重度或修改搜索关键字
            </span>
            {hasActiveFilters && (
              <button
                type="button"
                data-testid="event-clear-filters"
                onClick={clearAllFilters}
                className={cn(
                  'mt-3 rounded-md border border-border bg-card px-2.5 py-1 text-xs transition',
                  TEXT.subtitle,
                  INTERACTIVE.hover,
                )}
              >
                清除全部筛选
              </button>
            )}
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
          className={cn('flex shrink-0 items-center justify-between border-t bg-muted/50 px-3 py-2 text-xs', TEXT.subtitle)}
        >
          <span>
            第 <b className={cn('font-mono', TEXT.body)}>{from}-{to}</b> / 共{' '}
            <b className={cn('font-mono', TEXT.body)}>{total}</b> 条
          </span>
          <div className="flex items-center gap-1">
            {onExportCsv && (
              <button
                type="button"
                data-testid="event-export-csv"
                onClick={onExportCsv}
                disabled={isExporting}
                title="导出当前筛选与搜索命中的全部事件（CSV）"
                className={cn(
                  'mr-1 inline-flex items-center gap-0.5 rounded border px-2 py-0.5 transition disabled:opacity-40',
                  INTERACTIVE.hover,
                )}
              >
                <Download aria-hidden className="h-3 w-3" />
                {isExporting ? '导出中…' : '导出 CSV'}
              </button>
            )}
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
