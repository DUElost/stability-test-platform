import { useEffect, useMemo, useState } from 'react';
import { RefreshCw, AlertTriangle, ChevronDown, ChevronUp } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { StatusBadge } from '@/components/ui/status-badge';
import {
  ALERT_BANNER,
  ELEVATION,
  INTERACTIVE,
  SCRIPT_MATCH_ROW,
  TEXT,
} from '@/design-system';
import { cn } from '@/lib/utils';
import { formatIsoCompact } from '@/utils/format';
import type { PlanDispatchState, PrecheckState } from '@/utils/api/types';

interface Props {
  precheck: PrecheckState | null | undefined;
  dispatchState?: PlanDispatchState | null;
  /** Whether the parent PlanRun is in a terminal status. */
  isTerminal: boolean;
  /** Manual retry after precheck / sync failure. */
  onRetryDispatch?: () => void;
  isRetrying?: boolean;
  retryable?: boolean;
}

const PHASE_SPIN: ReadonlyArray<string> = ['verifying', 'syncing', 'reverifying'];

function shortSha(sha?: string | null): string {
  if (!sha) return '—';
  return sha.length <= 8 ? sha : sha.slice(0, 8) + '…';
}

function getReadySuffix(
  dispatchStatus?: string | null,
  isTerminal?: boolean,
): string | null {
  if (dispatchStatus === 'completed' || isTerminal) return '派发完成';
  if (dispatchStatus === 'running') return '派发中';
  if (dispatchStatus === 'queued') return '等待派发';
  return null;
}

const GATE_STALE_SECONDS = 90;

export function gateElapsedSeconds(
  dispatchState: Props['dispatchState'],
  precheck?: Pick<PrecheckState, 'started_at'> | null,
  nowMs: number = Date.now(),
): number | null {
  const ts = dispatchState?.started_at ?? dispatchState?.enqueued_at ?? precheck?.started_at;
  if (!ts) return null;
  const startMs = new Date(ts).getTime();
  if (Number.isNaN(startMs)) return null;
  return (nowMs - startMs) / 1000;
}

export function isGateStale(
  dispatchState: Props['dispatchState'],
  precheck: PrecheckState | null | undefined,
  isTerminal: boolean,
  nowMs: number = Date.now(),
): boolean {
  if (isTerminal) return false;
  if (typeof dispatchState?.stale === 'boolean') return dispatchState.stale;
  if (dispatchState?.deadline_at) {
    const deadlineMs = new Date(dispatchState.deadline_at).getTime();
    if (!Number.isNaN(deadlineMs)) return nowMs >= deadlineMs;
  }

  const dispatchStatus = dispatchState?.status;
  const precheckActive =
    !!precheck && precheck.phase !== 'ready' && precheck.phase !== 'failed';
  const dispatchActive =
    dispatchStatus === 'queued' || dispatchStatus === 'running';

  if (!precheckActive && !dispatchActive) return false;

  const elapsed = gateElapsedSeconds(dispatchState, precheck, nowMs);
  return elapsed !== null && elapsed > GATE_STALE_SECONDS;
}

export default function DispatchGateCard({
  precheck,
  dispatchState,
  isTerminal,
  onRetryDispatch,
  isRetrying = false,
  retryable,
  nowMs = Date.now(),
}: Props & { nowMs?: number }) {
  const dispatchOnly = !precheck;
  const gate: PrecheckState = precheck ?? {
    phase:
      dispatchState?.status === 'failed'
        ? 'failed'
        : dispatchState?.status === 'completed'
          ? 'ready'
          : 'verifying',
    started_at: dispatchState?.started_at ?? dispatchState?.enqueued_at ?? '',
    completed_at: dispatchState?.completed_at,
    hosts: {},
    final_result: dispatchState?.status === 'failed' ? 'failed' : null,
    errors: dispatchState?.last_error ? [dispatchState.last_error] : [],
  };

  const readySuffix =
    gate.phase === 'ready'
      ? getReadySuffix(dispatchState?.status, isTerminal)
      : null;
  const isPhaseSpinning = PHASE_SPIN.includes(gate.phase);
  const hostEntries = Object.entries(gate.hosts);
  const totalHosts = hostEntries.length;
  const isCompactReady =
    !isTerminal &&
    dispatchState?.status === 'completed' &&
    (gate.phase === 'ready' || dispatchOnly);
  const showStaleBanner = isGateStale(dispatchState, precheck, isTerminal, nowMs);
  const staleElapsedSec = Math.floor(gateElapsedSeconds(dispatchState, precheck, nowMs) ?? 0);
  const canRetryDispatch =
    !isRetrying &&
    (retryable ??
      dispatchState?.retryable ??
      (gate.phase === 'failed' || dispatchState?.status === 'failed'));
  const mixedWatcherFailure =
    gate.gate_failure?.code === 'MIXED_WATCHER_ACTIVITY'
      ? gate.gate_failure
      : null;

  // Aggregate counts for the summary line
  const counts = useMemo(() => {
    const out = { pending: 0, ok: 0, syncing: 0, synced: 0, failed: 0 };
    for (const [, h] of hostEntries) out[h.status] += 1;
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [gate]);

  // ── Collapse logic: auto-hide per-host details when all pass ──────────
  // isCompactReady: gate completely done → full compact (already implemented)
  // allHealthy: every host ok/synced, no errors → eligible for auto-collapse
  const allHealthy =
    (gate.phase === 'ready' || gate.phase === 'failed') &&
    counts.failed === 0 &&
    !gate.errors?.length &&
    hostEntries.every(([, h]) => h.status === 'ok' || h.status === 'synced');

  // Default: collapse when all healthy, expand when failures need attention.
  // Re-sync whenever allHealthy flips (a recovered gate auto-collapses, a newly
  // failed gate auto-expands); within a health state the user's toggle sticks.
  const [expanded, setExpanded] = useState(!allHealthy);
  useEffect(() => {
    setExpanded(!allHealthy);
  }, [allHealthy]);

  // Count total scripts
  const totalScriptCount = useMemo(
    () => hostEntries.reduce((sum, [, h]) => sum + h.scripts.length, 0),
    [hostEntries],
  );
  const allMatched = useMemo(
    () => hostEntries.every(([, h]) => h.scripts.every((s) => s.ok)),
    [hostEntries],
  );

  return (
    <div
      data-testid="dispatch-gate-card"
      className={cn('rounded-xl border bg-card', ELEVATION.sm)}
    >
      <div className="flex flex-wrap items-center gap-3 border-b px-4 py-3">
        <div className="inline-flex items-center gap-1.5">
          <StatusBadge
            kind="precheck-phase"
            status={gate.phase}
            size="sm"
            spin={isPhaseSpinning}
          />
          {readySuffix && (
            <span className="text-xs font-semibold text-success">· {readySuffix}</span>
          )}
        </div>
        <span className={cn('text-xs', TEXT.subtitle)}>
          {dispatchOnly
            ? '派发状态'
            : `派发门禁 · ${totalHosts} 主机 · ${counts.ok + counts.synced}/${totalHosts} 通过`}
          {counts.failed > 0 && (
            <span className="ml-2 font-semibold text-destructive">
              失败 {counts.failed}
            </span>
          )}
        </span>
        {allHealthy && !isCompactReady && (
          <button
            type="button"
            data-testid="dispatch-gate-toggle"
            onClick={() => setExpanded((v) => !v)}
            className={cn(
              'ml-auto inline-flex items-center gap-1 rounded px-2 py-0.5 text-xs transition',
              INTERACTIVE.iconButton,
              INTERACTIVE.hover,
            )}
          >
            {expanded ? (
              <><ChevronUp className="h-3 w-3" />收起详情</>
            ) : (
              <><ChevronDown className="h-3 w-3" />展开详情</>
            )}
          </button>
        )}
        {gate.errors && gate.errors.length > 0 && (
          <span className="ml-auto inline-flex items-center gap-1 text-xs text-destructive">
            <AlertTriangle className="h-3.5 w-3.5" />
            {gate.errors[gate.errors.length - 1]}
          </span>
        )}
        {canRetryDispatch && onRetryDispatch && (
          <Button
            type="button"
            variant="destructive"
            size="sm"
            data-testid="dispatch-gate-retry-button"
            onClick={onRetryDispatch}
            disabled={isRetrying}
            className="ml-auto h-7 gap-1 px-2.5 text-xs"
          >
            <RefreshCw className={cn('h-3.5 w-3.5', isRetrying && 'animate-spin')} />
            重试派发
          </Button>
        )}
      </div>

      {showStaleBanner && (
        <div
          data-testid="dispatch-gate-stale-banner"
          className={cn('flex items-start gap-2 px-4 py-2.5 text-xs', ALERT_BANNER.warning)}
        >
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span>
            派发门禁已运行 {staleElapsedSec}s（
            {dispatchState?.deadline_at ? '已超过后端截止时间' : '超过 90s 兼容阈值'}）。若长时间无 Job 出现，请检查
            SAQ Worker / Redis 或等待 precheck reaper 补偿。
          </span>
        </div>
      )}

      {mixedWatcherFailure && mixedWatcherFailure.inactive_host_ids.length > 0 && (
        <div
          data-testid="dispatch-gate-mixed-watcher-detail"
          className={cn('px-4 py-2.5 text-xs', ALERT_BANNER.destructive)}
        >
          不激活节点ID：{mixedWatcherFailure.inactive_host_ids.join(', ')}
        </div>
      )}

      {(dispatchState || isCompactReady) && (
        <div className={cn('border-b bg-muted/50 px-4 py-3')} data-testid="dispatch-gate-summary">
          <div className="flex flex-wrap items-center gap-2">
            <span className={cn('text-xs font-semibold', TEXT.body)}>
              派发摘要
            </span>
            {isCompactReady && (
              <span className="text-xs text-success">门禁通过，活跃 run 保留摘要态展示</span>
            )}
          </div>
          <div className={cn('mt-2 grid grid-cols-1 gap-2 text-xs sm:grid-cols-2 xl:grid-cols-5', TEXT.subtitle)}>
            <div>
              <span className="text-muted-foreground/70">状态</span>
              <div className={cn('font-mono', TEXT.body)}>
                {dispatchState?.status ?? '—'}
              </div>
            </div>
            <div>
              <span className="text-muted-foreground/70">入队</span>
              <div className={cn('font-mono', TEXT.body)}>
                {formatIsoCompact(dispatchState?.enqueued_at)}
              </div>
            </div>
            <div>
              <span className="text-muted-foreground/70">开始</span>
              <div className={cn('font-mono', TEXT.body)}>
                {formatIsoCompact(dispatchState?.started_at)}
              </div>
            </div>
            <div>
              <span className="text-muted-foreground/70">完成</span>
              <div className={cn('font-mono', TEXT.body)}>
                {formatIsoCompact(dispatchState?.completed_at)}
              </div>
            </div>
            <div>
              <span className="text-muted-foreground/70">最近错误</span>
              <div
                className={cn(
                  'break-all font-mono',
                  dispatchState?.last_error ? 'text-destructive' : TEXT.body,
                )}
              >
                {dispatchState?.last_error ?? '—'}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Per-host details: hidden when fully compact, collapsed when all-healthy, expanded on failure */}
      {!isCompactReady && !dispatchOnly && (
        <div className="divide-y">
          {allHealthy && !expanded ? (
            <div
              data-testid="dispatch-gate-collapsed"
              className={cn('px-4 py-3 text-xs', TEXT.subtitle)}
            >
              <span className="font-semibold text-success">✓ {totalHosts} 台主机</span>
              <span className="ml-2">· {totalScriptCount} 个脚本</span>
              {allMatched && <span className="ml-2 text-success">全部匹配</span>}
            </div>
          ) : hostEntries.length === 0 ? (
            <div className={cn('px-4 py-8 text-center text-xs', TEXT.subtitle)}>
              未解析出主机
            </div>
          ) : (
            hostEntries.map(([hostId, state]) => {
              const totalScripts = state.scripts.length;
              const matchedScripts = state.scripts.filter((s) => s.ok).length;
              return (
                <div
                  key={hostId}
                  data-testid={`dispatch-gate-host-${hostId}`}
                  className="px-4 py-3"
                >
                  <div className="flex flex-wrap items-center gap-3">
                    <span className={cn('font-mono text-sm font-semibold', TEXT.body)}>
                      {hostId}
                    </span>
                    <StatusBadge
                      kind="precheck-host"
                      status={state.status}
                      size="sm"
                      spin={state.status === 'syncing'}
                    />
                    {totalScripts > 0 && (
                      <span className={cn('text-xs', TEXT.subtitle)}>
                        {matchedScripts}/{totalScripts} 脚本一致
                      </span>
                    )}
                    {state.sync_attempts > 0 && (
                      <span className="text-xs text-muted-foreground/70">
                        sync ×{state.sync_attempts}
                        {gate.sync_max_attempts != null
                          ? `/${gate.sync_max_attempts}`
                          : ''}
                      </span>
                    )}
                    {state.error && (
                      <span className="ml-auto text-xs text-destructive">
                        {state.error}
                      </span>
                    )}
                  </div>

                  {totalScripts > 0 && (
                    <div className="mt-2 grid grid-cols-1 gap-1 text-xs sm:grid-cols-2 lg:grid-cols-3">
                      {state.scripts.map((s, idx) => (
                        <div
                          key={`${s.name}-${s.version}-${idx}`}
                          className={cn(
                            'flex items-center justify-between gap-2 rounded px-2 py-1',
                            s.ok ? SCRIPT_MATCH_ROW.ok : SCRIPT_MATCH_ROW.fail,
                          )}
                        >
                          <span className="truncate font-mono">
                            {s.name}@{s.version}
                          </span>
                          <span className={cn('shrink-0 font-mono text-[11px]', TEXT.subtitle)}>
                            {s.ok ? '✓ ' : '✗ '}
                            {shortSha(s.actual_sha)}
                          </span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              );
            })
          )}
        </div>
      )}
    </div>
  );
}
