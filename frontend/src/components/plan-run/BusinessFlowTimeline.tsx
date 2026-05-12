import { useMemo, useState } from 'react';
import {
  Check,
  Loader2,
  Circle,
  AlertTriangle,
  Activity,
  XCircle,
  Info,
  CheckCircle2,
  Clock,
  ShieldCheck,
  ShieldX,
} from 'lucide-react';
import type {
  EventSeverity,
  EventStage,
  PlanRunEvent,
  PlanRunEventsPayload,
  PlanRunTimeline,
  TimelineStage,
} from '@/utils/api/types';

interface Props {
  timeline: PlanRunTimeline | undefined;
  events: PlanRunEventsPayload | undefined;
  /** Selected stage filter; lifted up so the parent can sync URL params later. */
  stageFilter?: EventStage | 'all';
  severityFilter?: EventSeverity | 'all';
  onStageFilterChange?: (s: EventStage | 'all') => void;
  onSeverityFilterChange?: (s: EventSeverity | 'all') => void;
  isLoading?: boolean;
  /** precheck state from run_context — renders above INIT in the stepper */
  precheck?: Record<string, any> | null;
  dispatchState?: Record<string, any> | null;
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

const SEVERITY_CLS: Record<EventSeverity, { dot: string; label: string }> = {
  ok: { dot: 'bg-green-500', label: '完成' },
  info: { dot: 'bg-blue-500', label: '信息' },
  warn: { dot: 'bg-amber-500', label: '告警' },
  err: { dot: 'bg-red-500', label: '异常' },
};

const SEVERITY_ICON: Record<EventSeverity, React.ElementType> = {
  ok: CheckCircle2,
  info: Info,
  warn: AlertTriangle,
  err: XCircle,
};

const STAGE_CHIP_CLS: Record<EventStage, string> = {
  trigger: 'bg-purple-100 text-purple-700',
  init: 'bg-blue-100 text-blue-700',
  patrol: 'bg-orange-100 text-orange-700',
  teardown: 'bg-gray-100 text-gray-700',
  system: 'bg-slate-100 text-slate-700',
};

const STAGE_CHIP_LABEL: Record<EventStage, string> = {
  trigger: '触发',
  init: 'INIT',
  patrol: 'PATROL',
  teardown: 'TEARDOWN',
  system: '系统',
};

function fmtTs(ts: string): string {
  if (!ts) return '';
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return ts;
  return d.toLocaleTimeString('zh-CN', { hour12: false });
}

function fmtDuration(seconds: number | null | undefined): string {
  if (!seconds || !isFinite(seconds) || seconds <= 0) return '';
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  if (m === 0) return `${s}s`;
  if (m < 60) return `${m}m ${s}s`;
  return `${Math.floor(m / 60)}h ${m % 60}m`;
}

// ── Left column: vertical stepper ─────────────────────────────────────────

/** Compact precheck row that sits above the INIT/PATROL/TEARDOWN stages
 *  in the left stepper column.  Replaces the standalone DispatchGateCard. */
function PrecheckRow({
  precheck,
  dispatchState,
}: {
  precheck?: Record<string, any> | null;
  dispatchState?: Record<string, any> | null;
}) {
  if (!precheck) return null;

  const phase = precheck.phase ?? 'unknown';
  const finalResult = precheck.final_result;
  const hosts = (precheck.hosts ?? {}) as Record<string, Record<string, any>>;
  const hostEntries = Object.entries(hosts);
  const totalHosts = hostEntries.length;
  const okHosts = hostEntries.filter(([, h]) => h.status === 'ok').length;
  const syncingHosts = hostEntries.filter(([, h]) => h.status === 'syncing').length;
  const failedHosts = hostEntries.filter(([, h]) => h.status === 'failed').length;

  // Compute total scripts and verified count
  let totalScripts = 0;
  let verifiedScripts = 0;
  for (const [, h] of hostEntries) {
    const scripts = (h.scripts ?? []) as Array<Record<string, any>>;
    totalScripts += scripts.length;
    verifiedScripts += scripts.filter((s) => s.ok).length;
  }

  const isDone = finalResult === 'ready' || phase === 'ready';
  const isRunning = phase === 'verifying' || phase === 'syncing' || dispatchState?.status === 'running';
  const isFailed = phase === 'failed' || finalResult === 'failed' || failedHosts > 0;

  let nodeIcon: React.ElementType = Clock;
  let nodeCls = 'border-blue-400 text-blue-600 bg-blue-50';
  let cardCls = 'border-blue-200 bg-blue-50/50';
  let statusText = '等待中';
  let statusColor = 'text-blue-600';

  if (isDone) {
    nodeIcon = ShieldCheck;
    nodeCls = 'border-green-500 text-green-600 bg-green-50';
    cardCls = 'border-green-300 bg-green-50/50';
    statusText = '✓ 通过';
    statusColor = 'text-green-600';
  } else if (isFailed) {
    nodeIcon = ShieldX;
    nodeCls = 'border-red-500 text-red-600 bg-red-50';
    cardCls = 'border-red-300 bg-red-50/50';
    statusText = '✗ 失败';
    statusColor = 'text-red-600';
  } else if (isRunning) {
    nodeIcon = Loader2;
    nodeCls = 'border-amber-500 text-white bg-amber-500';
    cardCls = 'border-amber-400 bg-gradient-to-b from-amber-50 to-white ring-2 ring-amber-200';
    statusText = '⟳ ' + (phase === 'syncing' ? '同步中' : '校验中');
    statusColor = 'text-amber-600';
  }

  const NodeIcon = nodeIcon;

  return (
    <div data-testid="precheck-row" className="relative grid grid-cols-[24px_1fr] gap-3 py-1.5">
      <div className="relative flex justify-center">
        <span
          className={`relative z-10 flex h-5 w-5 items-center justify-center rounded-full border-2 ${nodeCls}`}
        >
          <NodeIcon className={`h-3 w-3 ${isRunning && !isDone ? 'animate-spin' : ''}`} />
        </span>
        <span className={`absolute left-1/2 top-5 -bottom-2 w-px -translate-x-1/2 ${isDone ? 'bg-green-300' : isFailed ? 'bg-red-300' : 'bg-gray-200'}`} />
      </div>
      <div className={`flex flex-col gap-1 rounded-lg border px-3 py-2 ${cardCls}`}>
        <div className="flex items-center gap-2">
          <span className="rounded bg-violet-100 px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wider text-violet-700">
            预检
          </span>
          <span className="flex-1 truncate text-sm font-semibold text-gray-900">
            健康预检
          </span>
          <span className={`text-[11px] font-medium ${statusColor}`}>
            {statusText}
          </span>
        </div>
        <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] text-gray-500">
          <span>
            <b className="font-semibold text-gray-800">{totalHosts}</b> 主机
          </span>
          <span>
            <b
              className={`font-semibold ${
                verifiedScripts === totalScripts && totalScripts > 0
                  ? 'text-green-700'
                  : 'text-gray-800'
              }`}
            >
              {verifiedScripts}/{totalScripts}
            </b>{' '}
            脚本
          </span>
          {okHosts > 0 && isDone && (
            <span className="text-green-600">
              <b className="font-semibold">{okHosts}</b> 就绪
            </span>
          )}
          {failedHosts > 0 && (
            <span className="text-red-600">
              <b className="font-semibold">{failedHosts}</b> 失败
            </span>
          )}
          {syncingHosts > 0 && (
            <span className="text-amber-600">
              <b className="font-semibold">{syncingHosts}</b> 同步中
            </span>
          )}
        </div>
        {/* Host x script mini-detail (always visible when there's data) */}
        {hostEntries.length > 0 && (
          <div className="mt-1 space-y-0.5 border-t border-gray-200/70 pt-1.5 text-[10.5px]">
            {hostEntries.map(([hid, h]) => {
              const scripts = (h.scripts ?? []) as Array<Record<string, any>>;
              const hOk = scripts.filter((s) => s.ok).length;
              return (
                <div key={hid} className="flex items-center gap-1.5 text-gray-500">
                  <span
                    className={`h-1.5 w-1.5 shrink-0 rounded-full ${
                      h.status === 'ok' ? 'bg-green-500' :
                      h.status === 'failed' ? 'bg-red-500' :
                      h.status === 'syncing' ? 'bg-amber-500' :
                      'bg-gray-300'
                    }`}
                  />
                  <span className="font-mono text-[10px] truncate" title={hid}>
                    {hid.length > 20 ? hid.slice(-20) : hid}
                  </span>
                  <span className="ml-auto shrink-0">
                    {hOk}/{scripts.length} 匹配
                    {h.error && (
                      <span className="ml-1 text-red-500">{h.error}</span>
                    )}
                  </span>
                </div>
              );
            })}
          </div>
        )}
      </div>
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
  let nodeCls = 'border-gray-300 text-gray-400 bg-white';
  let cardCls = 'border-gray-200 bg-white';
  if (stage.status === 'completed') {
    nodeIcon = Check;
    nodeCls = 'border-green-500 text-green-600 bg-green-50';
    cardCls = 'border-green-300 bg-green-50/50';
  } else if (stage.status === 'failed') {
    nodeIcon = XCircle;
    nodeCls = 'border-red-500 text-red-600 bg-red-50';
    cardCls = 'border-red-300 bg-red-50/50';
  } else if (stage.status === 'skipped') {
    nodeCls = 'border-gray-300 text-gray-400 bg-gray-100';
    cardCls = 'border-gray-300 bg-gray-50';
  }
  if (isCurrent) {
    nodeIcon = Loader2;
    nodeCls = 'border-orange-500 text-white bg-orange-500';
    cardCls = 'border-orange-400 bg-gradient-to-b from-orange-50 to-white ring-2 ring-orange-200';
  }
  if (isActive) {
    cardCls = cardCls + ' ring-2 ring-blue-300';
  }
  const NodeIcon = nodeIcon;

  return (
    <div data-testid={`stage-row-${stage.stage}`} className="relative grid grid-cols-[24px_1fr] gap-3 py-1.5">
      <div className="relative flex justify-center">
        <span className={`relative z-10 flex h-5 w-5 items-center justify-center rounded-full border-2 ${nodeCls}`}>
          <NodeIcon className={`h-3 w-3 ${isCurrent ? 'animate-spin' : ''}`} />
        </span>
        <span className="absolute left-1/2 top-5 -bottom-2 w-px -translate-x-1/2 bg-gray-200" />
      </div>
      <button type="button" onClick={onClick} className={`flex flex-col gap-1 rounded-lg border px-3 py-2 text-left transition-shadow ${cardCls} hover:shadow-sm`}>
        <div className="flex items-center gap-2">
          <span className="rounded bg-gray-200/70 px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wider text-gray-600">{STAGE_LABEL[stage.stage]}</span>
          <span className="flex-1 truncate text-sm font-semibold text-gray-900">{STAGE_TITLE[stage.stage]}</span>
          <span className="text-[11px] font-medium text-gray-600">{STAGE_STATUS_LABEL[stage.status]}</span>
        </div>
        <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] text-gray-500">
          {stage.device_succeeded > 0 && <span><b className="font-semibold text-gray-800">{stage.device_succeeded}</b> 就绪</span>}
          {stage.device_failed > 0 && <span className="text-red-600"><b className="font-semibold">{stage.device_failed}</b> 失败</span>}
          {(stage.device_skipped ?? 0) > 0 && <span className="text-gray-400"><b className="font-semibold">{stage.device_skipped}</b> 跳过</span>}
          <span><b className="font-semibold text-gray-800">{stage.steps.length}</b> 步骤</span>
          {stage.duration_seconds != null && <span>{fmtDuration(stage.duration_seconds)}</span>}
          {stage.stage === 'patrol' && stage.patrol_cycle_index != null && (
            <span>周期 <b className="font-semibold text-gray-800">#{stage.patrol_cycle_index}</b>
              {stage.patrol_interval_seconds && <span className="text-gray-400"> · interval {stage.patrol_interval_seconds}s</span>}
            </span>
          )}
        </div>
        {/* Step names — always visible */}
        {stage.steps.length > 0 && (
          <div className="flex flex-wrap gap-x-3 text-[11px] text-gray-500">
            {stage.steps.map((s) => (
              <span key={s.step_key}>{s.script_name || s.step_key} <span className="text-gray-400">{s.device_succeeded}/{s.device_total}{s.device_failed > 0 && <span className="ml-0.5 text-red-500">· {s.device_failed} 失败</span>}</span></span>
            ))}
          </div>
        )}
      </button>
    </div>
  );
}

// ── Right column: events stream ───────────────────────────────────────────

function EventRow({ event }: { event: PlanRunEvent }) {
  const Icon = SEVERITY_ICON[event.severity];
  return (
    <div
      data-testid={`event-row-${event.ts}-${event.category}`}
      className="grid grid-cols-[64px_18px_1fr_auto] items-start gap-2 border-b border-gray-100 px-3 py-2 text-xs last:border-b-0 hover:bg-gray-50/60"
    >
      <span className="font-mono text-[10.5px] text-gray-400">
        {fmtTs(event.ts)}
      </span>
      <span className="flex justify-center pt-0.5">
        <Icon
          className={`h-3.5 w-3.5 ${
            event.severity === 'err'
              ? 'text-red-500'
              : event.severity === 'warn'
              ? 'text-amber-500'
              : event.severity === 'ok'
              ? 'text-green-500'
              : 'text-blue-500'
          }`}
        />
      </span>
      <div className="min-w-0">
        <div className="truncate font-medium text-gray-900">{event.title}</div>
        {event.description && (
          <div className="mt-0.5 line-clamp-2 text-[11px] leading-snug text-gray-500">
            {event.description}
          </div>
        )}
        {(event.device_serial || event.job_id) && (
          <div className="mt-0.5 text-[10.5px] text-gray-400">
            {event.device_serial && (
              <span className="font-mono">{event.device_serial}</span>
            )}
            {event.job_id && <span className="ml-1">· Job #{event.job_id}</span>}
          </div>
        )}
      </div>
      <span
        className={`shrink-0 rounded px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wider ${
          STAGE_CHIP_CLS[event.stage]
        }`}
      >
        {STAGE_CHIP_LABEL[event.stage]}
      </span>
    </div>
  );
}

// ── Public component ──────────────────────────────────────────────────────

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
  precheck,
  dispatchState,
}: Props) {
  // Active stage for detail view (null = show events)
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

  return (
    <section data-testid="business-flow-timeline" className="space-y-2">
      {/* v3: abort summary banner */}
      {abortedCount > 0 && (
        <div
          data-testid="timeline-abort-banner"
          className="mx-1 flex items-center gap-2 rounded border-l-4 border-amber-400 bg-amber-50 px-3 py-2 text-xs text-amber-900"
        >
          <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
          <span>
            已中止{' '}
            <b className="font-mono">{abortedCount}</b>{' '}
            个 Job (abort 覆盖,PlanRun 强制 FAILED)
          </span>
        </div>
      )}

      <div className="mx-1 flex items-center gap-2.5">
        <span className="h-3 w-1 rounded-sm bg-gradient-to-b from-blue-600 to-blue-400" />
        <span className="text-xs font-bold uppercase tracking-wider text-gray-700">
          业务流时间线
        </span>
        <span className="text-[11px] text-gray-500">{headerMeta}</span>
      </div>
      <div className="grid grid-cols-1 gap-0 overflow-hidden rounded-xl border bg-white shadow-sm lg:grid-cols-[340px_1fr]">
        {/* Left: stepper */}
        <div className="border-b bg-gray-50/40 p-3 lg:border-b-0 lg:border-r">
          {isLoading && stages.length === 0 ? (
            <div className="px-2 py-6 text-center text-xs text-gray-400">
              加载中…
            </div>
          ) : stages.length === 0 ? (
            <div className="px-2 py-6 text-center text-xs text-gray-400">
              无阶段定义
            </div>
          ) : (
            <div className="relative space-y-0">
              <PrecheckRow precheck={precheck} dispatchState={dispatchState} />
              {stages.map((stage) => (
                <StageRow
                  key={stage.stage}
                  stage={stage}
                  isCurrent={currentStage === stage.stage}
                  isActive={activeStage === stage.stage}
                  onClick={() => setActiveStage(activeStage === stage.stage ? null : stage.stage)}
                />
              ))}
            </div>
          )}
        </div>

        {/* Right: events */}
        <div className="flex min-h-[340px] flex-col">
          <div className="flex flex-wrap items-center gap-1 border-b bg-white px-3 py-2">
            <span className="mr-1 text-[10px] font-semibold uppercase tracking-wider text-gray-400">
              阶段
            </span>
            {STAGE_FILTERS.map((f) => (
              <button
                key={f.key}
                type="button"
                data-testid={`event-filter-stage-${f.key}`}
                onClick={() => onStageFilterChange?.(f.key)}
                className={`rounded-md px-2 py-0.5 text-[11px] transition ${
                  stageFilter === f.key
                    ? 'bg-blue-100 font-semibold text-blue-700'
                    : 'text-gray-600 hover:bg-gray-100'
                }`}
              >
                {f.label}
                <span className="ml-1 text-[10px] text-gray-400">
                  {facetStage[f.key] ?? 0}
                </span>
              </button>
            ))}
            <span className="mx-2 h-3 w-px bg-gray-200" />
            <span className="mr-1 text-[10px] font-semibold uppercase tracking-wider text-gray-400">
              严重度
            </span>
            {SEVERITY_FILTERS.map((f) => (
              <button
                key={f.key}
                type="button"
                data-testid={`event-filter-sev-${f.key}`}
                onClick={() => onSeverityFilterChange?.(f.key)}
                className={`inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-[11px] transition ${
                  severityFilter === f.key
                    ? 'bg-blue-100 font-semibold text-blue-700'
                    : 'text-gray-600 hover:bg-gray-100'
                }`}
              >
                {f.key !== 'all' && (
                  <span
                    className={`h-1.5 w-1.5 rounded-full ${
                      SEVERITY_CLS[f.key as EventSeverity]?.dot ?? 'bg-gray-400'
                    }`}
                  />
                )}
                {f.label}
                <span className="text-[10px] text-gray-400">
                  {facetSev[f.key] ?? 0}
                </span>
              </button>
            ))}
          </div>

          <div className="flex-1 overflow-y-auto" data-testid="event-list">
            {isLoading && eventList.length === 0 ? (
              <div className="flex h-32 items-center justify-center text-xs text-gray-400">
                <Activity className="mr-1 h-3 w-3 animate-pulse" /> 加载事件…
              </div>
            ) : eventList.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-10 text-xs text-gray-400">
                该过滤条件下暂无事件
                <span className="mt-1 text-[10.5px] text-gray-300">
                  尝试切换阶段或严重度
                </span>
              </div>
            ) : (
              eventList.map((e, idx) => (
                <EventRow key={`${e.ts}-${e.category}-${idx}`} event={e} />
              ))
            )}
          </div>
        </div>
      </div>
    </section>
  );
}
