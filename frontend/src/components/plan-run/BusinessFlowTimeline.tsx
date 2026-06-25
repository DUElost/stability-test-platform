import { useMemo, useState } from 'react';
import {
  Check,
  Loader2,
  Circle,
  AlertTriangle,
  XCircle,
  Clock,
  ShieldCheck,
  ShieldX,
  ChevronDown,
  Minus,
  AlertCircle,
} from 'lucide-react';
import { Skeleton } from '@/components/ui/skeleton';
import {
  ALERT_BANNER,
  EVENT_SEVERITY_DOT,
  EVENT_STAGE_CHIP,
  FILTER_CHIP,
  PANEL,
  TEXT,
  TIMELINE_NODE,
  TIMELINE_STEP_ROW,
} from '@/design-system';
import { cn } from '@/lib/utils';
import { formatTimeLabel, formatDurationSeconds } from '@/utils/format';
import SectionHeader from './SectionHeader';
import type {
  EventSeverity,
  EventStage,
  PlanDispatchState,
  PlanRunEvent,
  PlanRunEventsPayload,
  PlanRunTimeline,
  PrecheckHostState,
  PrecheckScriptCheck,
  PrecheckState,
  TimelineStage,
} from '@/utils/api/types';

interface Props {
  timeline: PlanRunTimeline | undefined;
  events: PlanRunEventsPayload | undefined;
  stageFilter?: EventStage | 'all';
  severityFilter?: EventSeverity | 'all';
  onStageFilterChange?: (s: EventStage | 'all') => void;
  onSeverityFilterChange?: (s: EventSeverity | 'all') => void;
  isLoading?: boolean;
  isError?: boolean;
  precheck?: PrecheckState | null;
  dispatchState?: PlanDispatchState | null;
}

const STAGE_LABEL: Record<TimelineStage['stage'], string> = {
  init: 'INIT',
  patrol: 'PATROL',
  teardown: 'TEARDOWN',
};

const STAGE_TITLE: Record<TimelineStage['stage'], string> = {
  init: '前置准备',
  patrol: '巡检循环',
  teardown: '收尾清理',
};

const STAGE_STATUS_LABEL: Record<TimelineStage['status'], string> = {
  pending: '○ 等待',
  running: '⟳ 进行中',
  completed: '✓ 完成',
  failed: '✗ 失败',
  skipped: '— 跳过',
};

const SEVERITY_CLS: Record<EventSeverity, { dot: string; node: string; label: string }> = {
  ok: { dot: EVENT_SEVERITY_DOT.ok, node: EVENT_SEVERITY_DOT.ok, label: '完成' },
  info: { dot: EVENT_SEVERITY_DOT.info, node: EVENT_SEVERITY_DOT.info, label: '信息' },
  warn: { dot: EVENT_SEVERITY_DOT.warn, node: EVENT_SEVERITY_DOT.warn, label: '告警' },
  err: { dot: EVENT_SEVERITY_DOT.err, node: EVENT_SEVERITY_DOT.err, label: '异常' },
};

const STAGE_CHIP_CLS: Record<EventStage, string> = EVENT_STAGE_CHIP;

const STAGE_CHIP_LABEL: Record<EventStage, string> = {
  trigger: '触发',
  init: 'INIT',
  patrol: 'PATROL',
  teardown: 'TEARDOWN',
  system: '系统',
};

function fmtTs(ts: string): string {
  return formatTimeLabel(ts, '');
}

// ── Left column: vertical stepper ───────────────────────────────────────

function PrecheckRow({
  precheck,
  dispatchState,
  isActive,
  onClick,
}: {
  precheck?: PrecheckState | null;
  dispatchState?: PlanDispatchState | null;
  isActive?: boolean;
  onClick?: () => void;
}) {
  if (!precheck) return null;

  const phase = precheck.phase ?? 'unknown';
  const finalResult = precheck.final_result;
  const hosts: Record<string, PrecheckHostState> = precheck.hosts ?? {};
  const hostEntries = Object.entries(hosts);
  const totalHosts = hostEntries.length;
  const okHosts = hostEntries.filter(([, h]) => h.status === 'ok').length;
  const syncingHosts = hostEntries.filter(([, h]) => h.status === 'syncing').length;
  const failedHosts = hostEntries.filter(([, h]) => h.status === 'failed').length;

  let totalScripts = 0;
  let verifiedScripts = 0;
  for (const [, h] of hostEntries) {
    const scripts: PrecheckScriptCheck[] = h.scripts ?? [];
    totalScripts += scripts.length;
    verifiedScripts += scripts.filter((s) => s.ok).length;
  }

  const isDone = finalResult === 'ready' || phase === 'ready';
  const isRunning = phase === 'verifying' || phase === 'syncing' || dispatchState?.status === 'running';
  const isFailed = phase === 'failed' || finalResult === 'failed' || failedHosts > 0;

  let nodeIcon: React.ElementType = Clock;
  let nodeCls: string = TIMELINE_NODE.precheck.node;
  let cardCls: string = TIMELINE_NODE.precheck.card;
  let statusText = '等待中';
  let statusColor = 'text-info';

  if (isDone) {
    nodeIcon = ShieldCheck;
    nodeCls = TIMELINE_NODE.success.node;
    cardCls = TIMELINE_NODE.success.card;
    statusText = '✓ 通过';
    statusColor = 'text-success';
  } else if (isFailed) {
    nodeIcon = ShieldX;
    nodeCls = TIMELINE_NODE.failed.node;
    cardCls = TIMELINE_NODE.failed.card;
    statusText = '✗ 失败';
    statusColor = 'text-destructive';
  } else if (isRunning) {
    nodeIcon = Loader2;
    nodeCls = TIMELINE_NODE.running.node;
    cardCls = TIMELINE_NODE.running.card;
    statusText = '⟳ ' + (phase === 'syncing' ? '同步中' : '校验中');
    statusColor = 'text-warning';
  }

  const NodeIcon = nodeIcon;

  return (
    <div data-testid="precheck-row" className="relative grid grid-cols-[22px_1fr] gap-2.5 py-1">
      <div className="relative flex justify-center">
        <span className={cn('relative z-10 flex h-5 w-5 items-center justify-center rounded-full border-2 ring-[3px] ring-card', nodeCls)}>
          <NodeIcon className={cn('h-3 w-3', isRunning && !isDone && 'animate-spin')} />
        </span>
        <div className={cn('absolute top-5 bottom-0 left-1/2 w-px -translate-x-1/2', TIMELINE_NODE.precheck.connector)} />
      </div>
      <button
        type="button"
        onClick={onClick}
        className={cn(
          'flex flex-col gap-1 rounded-lg border px-3 py-2 text-left transition-all cursor-pointer',
          cardCls,
          isActive ? TIMELINE_NODE.active : TIMELINE_NODE.hover,
        )}
      >
        <div className="flex items-center gap-2">
          <span className={cn('rounded px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wider', TIMELINE_NODE.precheck.badge)}>
            预检
          </span>
          <span className={cn('flex-1 truncate text-sm font-semibold', TEXT.heading)}>
            健康预检
          </span>
          <span className={cn('text-xs font-semibold', statusColor)}>
            {statusText}
          </span>
          <ChevronDown className={cn('ml-auto h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform', isActive && 'rotate-180')} />
        </div>
        <div className={cn('flex flex-wrap gap-x-3 gap-y-0.5 text-xs', TEXT.subtitle)}>
          <span>
            <b className={cn('font-semibold', TEXT.body)}>{totalHosts}</b> 主机
          </span>
          <span>
            <b className={cn('font-semibold', verifiedScripts === totalScripts && totalScripts > 0 ? 'text-success' : TEXT.body)}>
              {verifiedScripts}/{totalScripts}
            </b> 脚本
          </span>
          {okHosts > 0 && isDone && <span className="text-success"><b className="font-semibold">{okHosts}</b> 就绪</span>}
          {failedHosts > 0 && <span className="text-destructive"><b className="font-semibold">{failedHosts}</b> 失败</span>}
          {syncingHosts > 0 && <span className="text-warning"><b className="font-semibold">{syncingHosts}</b> 同步中</span>}
        </div>
        {hostEntries.length > 0 && (
          <div className="mt-1 space-y-0.5 border-t border-border/60 pt-1.5 text-[11px]">
            {hostEntries.map(([hid, h]) => {
              const scripts: PrecheckScriptCheck[] = h.scripts ?? [];
              const hOk = scripts.filter((s) => s.ok).length;
              return (
                <div key={hid} className={cn('flex items-center gap-1.5', TEXT.subtitle)}>
                  <span className={cn(
                    'h-1.5 w-1.5 shrink-0 rounded-full',
                    h.status === 'ok' ? EVENT_SEVERITY_DOT.ok
                      : h.status === 'failed' ? EVENT_SEVERITY_DOT.err
                      : h.status === 'syncing' ? EVENT_SEVERITY_DOT.warn
                      : 'bg-muted-foreground/30',
                  )} />
                  <span className="font-mono text-[11px] truncate" title={hid}>{hid.length > 20 ? hid.slice(-20) : hid}</span>
                  <span className="ml-auto shrink-0">{hOk}/{scripts.length} 匹配{h.error && <span className="ml-1 text-destructive">{h.error}</span>}</span>
                </div>
              );
            })}
          </div>
        )}
      </button>
    </div>
  );
}

function StageRow({
  stage,
  isCurrent,
  isActive,
  onClick,
}: {
  stage: TimelineStage;
  isCurrent: boolean;
  isActive: boolean;
  onClick: () => void;
}) {
  let nodeIcon: React.ElementType = Circle;
  let nodeCls: string = TIMELINE_NODE.idle.node;
  let cardCls: string = TIMELINE_NODE.idle.card;
  let codeCls: string = TIMELINE_NODE.idle.badge;

  if (stage.status === 'completed') {
    nodeIcon = Check;
    nodeCls = TIMELINE_NODE.success.node;
    cardCls = TIMELINE_NODE.success.card;
    codeCls = TIMELINE_NODE.success.badge;
  } else if (stage.status === 'failed') {
    nodeIcon = XCircle;
    nodeCls = TIMELINE_NODE.failed.node;
    cardCls = TIMELINE_NODE.failed.card;
    codeCls = TIMELINE_NODE.failed.badge;
  } else if (stage.status === 'skipped') {
    nodeCls = TIMELINE_NODE.skipped.node;
    cardCls = TIMELINE_NODE.skipped.card;
  }
  if (isCurrent) {
    nodeIcon = Loader2;
    nodeCls = TIMELINE_NODE.running.node;
    cardCls = TIMELINE_NODE.running.card;
    codeCls = TIMELINE_NODE.running.badge;
  }
  const NodeIcon = nodeIcon;

  return (
    <div data-testid={`stage-row-${stage.stage}`} className="relative grid grid-cols-[22px_1fr] gap-2.5 py-1">
      <div className="relative flex justify-center">
        <span className={cn('relative z-10 flex h-5 w-5 items-center justify-center rounded-full border-2 ring-[3px] ring-card', nodeCls)}>
          <NodeIcon className={cn('h-3 w-3', isCurrent && 'animate-spin')} />
        </span>
        {stage.stage !== 'teardown' && (
          <div
            className={cn(
              'absolute top-5 bottom-0 left-1/2 w-px -translate-x-1/2',
              stage.stage === 'init' ? TIMELINE_NODE.connectorInit : TIMELINE_NODE.connectorPatrol,
            )}
          />
        )}
      </div>
      <button
        type="button"
        onClick={onClick}
        className={cn(
          'flex flex-col gap-1 rounded-lg border px-3 py-2 text-left transition-all cursor-pointer',
          cardCls,
          isActive ? TIMELINE_NODE.active : TIMELINE_NODE.hover,
        )}
      >
        <div className="flex items-center gap-2">
          <span className={cn('rounded px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wider', codeCls)}>{STAGE_LABEL[stage.stage]}</span>
          <span className={cn('flex-1 truncate text-sm font-semibold', TEXT.heading)}>{STAGE_TITLE[stage.stage]}</span>
          <span className={cn('text-xs font-semibold', TEXT.subtitle)}>{STAGE_STATUS_LABEL[stage.status]}</span>
          <ChevronDown className={cn('ml-auto h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform', isActive && 'rotate-180')} />
        </div>
        <div className={cn('flex flex-wrap gap-x-3 text-xs', TEXT.subtitle)}>
          {stage.stage === 'patrol' ? (
            <>
              {stage.device_succeeded > 0 && (
                <span title="累计各巡检周期的成功 step 次数">
                  <b className={cn('font-semibold', TEXT.body)}>{stage.device_succeeded}</b> 完成
                  <span className="text-muted-foreground/70">(累计)</span>
                </span>
              )}
              {stage.device_failed > 0 && (
                <span className="text-destructive" title="累计各巡检周期的失败 step 次数">
                  <b className="font-semibold">{stage.device_failed}</b> 失败
                </span>
              )}
            </>
          ) : (
            <>
              {stage.device_total > 0 && (
                <span><b className={cn('font-semibold', TEXT.body)}>{stage.device_succeeded}/{stage.device_total}</b> 就绪</span>
              )}
              {stage.device_failed > 0 && <span className="text-destructive"><b className="font-semibold">{stage.device_failed}</b> 失败</span>}
              {(stage.device_skipped ?? 0) > 0 && <span className="text-muted-foreground/70"><b className="font-semibold">{stage.device_skipped}</b> 跳过</span>}
            </>
          )}
          <span><b className={cn('font-semibold', TEXT.body)}>{stage.steps.length}</b> 步骤</span>
          {stage.started_at && <span className="text-muted-foreground/70" title="阶段开始时刻">起 {fmtTs(stage.started_at)}</span>}
          {stage.duration_seconds != null && (
            <span>{formatDurationSeconds(stage.duration_seconds, 'precise', '')}</span>
          )}
        </div>
        {stage.stage === 'patrol' && stage.patrol_cycle_index != null && (
          <div className="text-[11px] text-muted-foreground/70">
            周期 <b className={cn('font-semibold', TEXT.subtitle)}>#{stage.patrol_cycle_index}</b>
            {stage.patrol_active_devices != null && <span> · {stage.patrol_active_devices} 台活跃</span>}
            {stage.patrol_interval_seconds && <span> · interval {stage.patrol_interval_seconds}s</span>}
          </div>
        )}
      </button>
    </div>
  );
}

// ── Right column: events with timeline axis ──────────────────────────────

function EventRow({ event }: { event: PlanRunEvent }) {
  const [expanded, setExpanded] = useState(false);
  const sevCfg = SEVERITY_CLS[event.severity];
  return (
    <div data-testid={`event-row-${event.ts}-${event.category}`} className="grid grid-cols-[60px_16px_1fr_auto] items-start gap-2 border-b border-border/40 px-3 py-2.5 text-xs last:border-b-0 hover:bg-muted/30">
      <span className="pt-0.5 font-mono text-[11px] tabular-nums text-muted-foreground/70">{fmtTs(event.ts)}</span>
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

// ── Public component ─────────────────────────────────────────────────────

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

export default function BusinessFlowTimeline({
  timeline,
  events,
  stageFilter = 'all',
  severityFilter = 'all',
  onStageFilterChange,
  onSeverityFilterChange,
  isLoading = false,
  isError = false,
  precheck,
  dispatchState,
}: Props) {
  const [activeStage, setActiveStage] = useState<string | null>(null);

  const stages = timeline?.stages ?? [];
  const currentStage = timeline?.current_stage;

  const totalEvents = events?.facets?.by_stage?.all ?? events?.total ?? 0;
  const eventList = events?.events ?? [];

  const facetStage = events?.facets?.by_stage ?? {};
  const facetSev = events?.facets?.by_severity ?? {};

  const headerMeta = useMemo(() => {
    const stagesCount = stages.length;
    const cur = currentStage ? STAGE_LABEL[currentStage as TimelineStage['stage']] || currentStage.toUpperCase() : '—';
    return `${stagesCount} 阶段 · 当前 ${cur} · 共 ${totalEvents} 条`;
  }, [stages.length, currentStage, totalEvents]);

  const abortedCount = timeline?.aborted_job_count ?? 0;

  const selectedStage: TimelineStage | null = activeStage
    ? (stages.find((s) => s.stage === activeStage) ?? null)
    : null;

  // Stage step rows for the right panel when a stage is selected
  const stageStepRows = useMemo(() => {
    if (activeStage === '__precheck__' && precheck) {
      const hosts: Record<string, PrecheckHostState> = precheck.hosts ?? {};
      const rows: Array<{ key: string; element: React.ReactElement }> = [];
      for (const [hid, h] of Object.entries(hosts)) {
        const scripts: PrecheckScriptCheck[] = h.scripts ?? [];
        for (const s of scripts) {
          const ok = s.ok === true;
          rows.push({
            key: `${hid}-${s.name}-${s.version}`,
            element: (
              <div key={`${hid}-${s.name}-${s.version}`} className={TIMELINE_STEP_ROW.root}>
                <span className={TIMELINE_STEP_ROW.label}>步骤</span>
                <div className="relative flex justify-center pt-1.5">
                  <Minus className={TIMELINE_STEP_ROW.icon} />
                </div>
                <div className="min-w-0">
                  <div className={cn('truncate font-semibold', TEXT.heading)}>{s.name}@{s.version}</div>
                  <div className="mt-0.5 text-[11px] text-muted-foreground/70">{s.error || (ok ? '验证通过' : '不匹配')}</div>
                </div>
                <span className={cn('shrink-0 rounded border px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wider', TIMELINE_NODE.precheck.badge, 'border-info/20')}>预检</span>
              </div>
            ),
          });
        }
      }
      return rows;
    }

    if (!selectedStage) return null;
    return selectedStage.steps.map((s) => {
      const stageKey = selectedStage.stage;
      const chip = (STAGE_CHIP_CLS as Record<string, string>)[stageKey] || EVENT_STAGE_CHIP.teardown;
      const label = (STAGE_LABEL as Record<string, string>)[stageKey] || stageKey;
      return {
        key: s.step_key,
        element: (
          <div key={s.step_key} className={TIMELINE_STEP_ROW.root}>
            <span className={TIMELINE_STEP_ROW.label}>步骤</span>
            <div className="relative flex justify-center pt-1.5">
              <Minus className={TIMELINE_STEP_ROW.icon} />
            </div>
            <div className="min-w-0">
              <div className={cn('truncate font-semibold', TEXT.heading)}>{s.script_name || s.step_key}</div>
              <div className="mt-0.5 text-[11px] text-muted-foreground/70">
                {s.device_succeeded}/{s.device_total} 设备
                {s.device_failed > 0 && <span className="ml-1 text-destructive">· {s.device_failed} 失败</span>}
                {(s.device_skipped ?? 0) > 0 && <span className="ml-1 text-muted-foreground/70">· {s.device_skipped} 跳过</span>}
              </div>
            </div>
            <span className={cn('shrink-0 rounded border px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wider', chip)}>{label}</span>
          </div>
        ),
      };
    });
  }, [activeStage, precheck, selectedStage]);

  return (
    <section data-testid="business-flow-timeline" className="space-y-2">
      {abortedCount > 0 && (
        <div data-testid="timeline-abort-banner" className={cn('mx-1 flex items-center gap-2 rounded border-l-4 border-warning px-3 py-2 text-xs', ALERT_BANNER.warning)}>
          <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
          <span>已中止 <b className="font-mono">{abortedCount}</b> 个 Job (abort 覆盖,PlanRun 强制 FAILED)</span>
        </div>
      )}

      <SectionHeader
        title="业务流时间线"
        meta={headerMeta}
      />

      <div className={cn('grid grid-cols-1 gap-0 overflow-hidden lg:grid-cols-[280px_1fr]', PANEL.root)}>
        <div className="relative border-b bg-gradient-to-b from-card to-muted/30 p-3 lg:border-b-0 lg:border-r">
          {isError ? (
            <div className="flex flex-col items-center justify-center px-2 py-6 text-center">
              <AlertCircle className="mb-1 h-5 w-5 text-destructive/60" />
              <span className="text-xs font-semibold text-destructive">加载失败</span>
              <span className="mt-0.5 text-[11px] text-destructive/70">请检查网络连接或稍后重试</span>
            </div>
          ) : isLoading && stages.length === 0 ? (
            <div className="space-y-2 px-2 py-4">
              <Skeleton className="h-14 w-full" />
              <Skeleton className="h-14 w-full" />
              <Skeleton className="h-14 w-full" />
            </div>
          ) : stages.length === 0 ? (
            <div className={cn('px-2 py-6 text-center text-xs', TEXT.subtitle)}>无阶段定义</div>
          ) : (
            <div className="relative space-y-0">
              <PrecheckRow
                precheck={precheck}
                dispatchState={dispatchState}
                isActive={activeStage === '__precheck__'}
                onClick={() => setActiveStage(activeStage === '__precheck__' ? null : '__precheck__')}
              />
              {stages.map((stage) => (
                <StageRow
                  key={stage.stage}
                  stage={stage}
                  isCurrent={currentStage === stage.stage}
                  isActive={activeStage === stage.stage}
                  onClick={() => {
                    const next = activeStage === stage.stage ? null : stage.stage;
                    setActiveStage(next);
                    // Link the event stream to the chosen stage (collapse → all).
                    onStageFilterChange?.(next ?? 'all');
                  }}
                />
              ))}
            </div>
          )}
        </div>

        {/* Right: events + optional stage detail */}
        <div className="flex min-h-[340px] flex-col">
          {/* Filter bar */}
          <div className="flex flex-wrap items-center gap-1 border-b bg-card px-3 py-2">
            <span className={cn('mr-1 text-[11px] font-bold uppercase tracking-wider', TEXT.subtitle)}>阶段</span>
            {STAGE_FILTERS.map((f) => (
              <button
                key={f.key}
                type="button"
                data-testid={`event-filter-stage-${f.key}`}
                onClick={() => {
                  onStageFilterChange?.(f.key);
                  setActiveStage(
                    f.key === 'init' || f.key === 'patrol' || f.key === 'teardown'
                      ? f.key
                      : null,
                  );
                }}
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
                {f.key !== 'all' && <span className={cn('h-1.5 w-1.5 rounded-full', SEVERITY_CLS[f.key as EventSeverity]?.dot ?? 'bg-muted-foreground/40')} />}
                {f.label}
                <span className={FILTER_CHIP.count}>{facetSev[f.key] ?? 0}</span>
              </button>
            ))}
          </div>

          {/* Event list body */}
          <div className="flex-1 overflow-y-auto" data-testid="event-list">
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
                {stageStepRows?.map((r) => r.element)}
                {stageStepRows && stageStepRows.length > 0 && eventList.length > 0 && (
                  <div className="border-b border-dashed border-border mx-3" />
                )}
                {eventList.map((e, idx) => (
                  <EventRow key={`${e.ts}-${e.category}-${idx}`} event={e} />
                ))}
                {(events?.total ?? 0) > eventList.length && (
                  <div
                    data-testid="event-truncation-notice"
                    className={cn('border-t px-3 py-2 text-center text-[11px]', TEXT.subtitle)}
                  >
                    仅显示前 {eventList.length} 条 · 当前筛选共 {events?.total} 条,请用上方过滤缩小范围
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      </div>
    </section>
  );
}
