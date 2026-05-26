import { useMemo } from 'react';
import { RefreshCw, AlertTriangle } from 'lucide-react';
import { StatusBadge } from '@/components/ui/status-badge';
import type { PrecheckState } from '@/utils/api/types';

interface Props {
  precheck: PrecheckState | null | undefined;
  dispatchState?:
    | {
        status?: string | null;
        enqueued_at?: string | null;
        started_at?: string | null;
        completed_at?: string | null;
        last_error?: string | null;
      }
    | null
    | undefined;
  /** Whether the parent PlanRun is in a terminal status. */
  isTerminal: boolean;
  /** Manual retry after precheck / sync failure. */
  onRetryDispatch?: () => void;
  isRetrying?: boolean;
}

const PHASE_SPIN: ReadonlyArray<string> = ['verifying', 'syncing', 'reverifying'];

function shortSha(sha?: string | null): string {
  if (!sha) return '—';
  return sha.length <= 8 ? sha : sha.slice(0, 8) + '…';
}

function formatTimestamp(value?: string | null): string {
  if (!value) return '—';
  return value.replace('T', ' ').replace('Z', ' UTC');
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
  nowMs: number = Date.now(),
): number | null {
  if (!dispatchState) return null;
  const ts = dispatchState.started_at ?? dispatchState.enqueued_at;
  if (!ts) return null;
  const startMs = new Date(ts).getTime();
  if (Number.isNaN(startMs)) return null;
  return (nowMs - startMs) / 1000;
}

export function isGateStale(
  dispatchState: Props['dispatchState'],
  precheck: PrecheckState,
  isTerminal: boolean,
  nowMs: number = Date.now(),
): boolean {
  if (isTerminal) return false;

  const dispatchStatus = dispatchState?.status;
  const precheckActive =
    precheck.phase !== 'ready' && precheck.phase !== 'failed';
  const dispatchActive =
    dispatchStatus === 'queued' || dispatchStatus === 'running';

  if (!precheckActive && !dispatchActive) return false;

  const elapsed = gateElapsedSeconds(dispatchState, nowMs);
  return elapsed !== null && elapsed > GATE_STALE_SECONDS;
}

export default function DispatchGateCard({
  precheck,
  dispatchState,
  isTerminal,
  onRetryDispatch,
  isRetrying = false,
  nowMs = Date.now(),
}: Props & { nowMs?: number }) {
  // Don't render at all when:
  //   1) there is no precheck context (PlanRun pre-dates ADR-0021).
  if (!precheck) return null;

  const readySuffix =
    precheck.phase === 'ready'
      ? getReadySuffix(dispatchState?.status, isTerminal)
      : null;
  const isPhaseSpinning = PHASE_SPIN.includes(precheck.phase);
  const hostEntries = Object.entries(precheck.hosts);
  const totalHosts = hostEntries.length;
  const isCompactReady =
    !isTerminal &&
    precheck.phase === 'ready' &&
    dispatchState?.status === 'completed';
  const showStaleBanner = isGateStale(dispatchState, precheck, isTerminal, nowMs);
  const staleElapsedSec = Math.floor(gateElapsedSeconds(dispatchState, nowMs) ?? 0);
  const canRetryDispatch =
    !isRetrying &&
    (precheck.phase === 'failed' || dispatchState?.status === 'failed');

  // Aggregate counts for the summary line
  const counts = useMemo(() => {
    const out = { pending: 0, ok: 0, syncing: 0, synced: 0, failed: 0 };
    for (const [, h] of hostEntries) out[h.status] += 1;
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [precheck]);

  return (
    <div
      data-testid="dispatch-gate-card"
      className="rounded-xl border bg-white shadow-sm"
    >
      <div className="flex flex-wrap items-center gap-3 border-b px-4 py-3">
        <div className="inline-flex items-center gap-1.5">
          <StatusBadge
            kind="precheck-phase"
            status={precheck.phase}
            size="sm"
            spin={isPhaseSpinning}
          />
          {readySuffix && (
            <span className="text-xs font-semibold text-green-700">· {readySuffix}</span>
          )}
        </div>
        <span className="text-xs text-gray-500">
          派发门禁 · {totalHosts} 主机 · {counts.ok + counts.synced}/{totalHosts} 通过
          {counts.failed > 0 && (
            <span className="ml-2 font-semibold text-red-600">
              失败 {counts.failed}
            </span>
          )}
        </span>
        {precheck.errors && precheck.errors.length > 0 && (
          <span className="ml-auto inline-flex items-center gap-1 text-xs text-red-600">
            <AlertTriangle className="h-3.5 w-3.5" />
            {precheck.errors[precheck.errors.length - 1]}
          </span>
        )}
        {canRetryDispatch && onRetryDispatch && (
          <button
            type="button"
            data-testid="dispatch-gate-retry-button"
            onClick={onRetryDispatch}
            className="ml-auto inline-flex items-center gap-1 rounded-md bg-red-600 px-2.5 py-1 text-xs font-semibold text-white hover:bg-red-700 disabled:opacity-50"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${isRetrying ? 'animate-spin' : ''}`} />
            重试派发
          </button>
        )}
      </div>

      {showStaleBanner && (
        <div
          data-testid="dispatch-gate-stale-banner"
          className="flex items-start gap-2 border-b border-amber-200 bg-amber-50 px-4 py-2.5 text-xs text-amber-900"
        >
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span>
            派发门禁已运行 {staleElapsedSec}s（超过 90s 阈值）。若长时间无 Job 出现，请检查
            SAQ Worker / Redis 或等待 precheck reaper 补偿。
          </span>
        </div>
      )}

      {(dispatchState || isCompactReady) && (
        <div className="border-b bg-gray-50/80 px-4 py-3" data-testid="dispatch-gate-summary">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs font-semibold text-gray-700">
              派发摘要
            </span>
            {isCompactReady && (
              <span className="text-xs text-green-700">
                门禁通过，活跃 run 保留摘要态展示
              </span>
            )}
          </div>
          <div className="mt-2 grid grid-cols-1 gap-2 text-xs text-gray-600 sm:grid-cols-2 xl:grid-cols-5">
            <div>
              <span className="text-gray-400">状态</span>
              <div className="font-mono text-gray-800">
                {dispatchState?.status ?? '—'}
              </div>
            </div>
            <div>
              <span className="text-gray-400">入队</span>
              <div className="font-mono text-gray-800">
                {formatTimestamp(dispatchState?.enqueued_at)}
              </div>
            </div>
            <div>
              <span className="text-gray-400">开始</span>
              <div className="font-mono text-gray-800">
                {formatTimestamp(dispatchState?.started_at)}
              </div>
            </div>
            <div>
              <span className="text-gray-400">完成</span>
              <div className="font-mono text-gray-800">
                {formatTimestamp(dispatchState?.completed_at)}
              </div>
            </div>
            <div>
              <span className="text-gray-400">最近错误</span>
              <div
                className={`break-all font-mono ${
                  dispatchState?.last_error ? 'text-red-600' : 'text-gray-800'
                }`}
              >
                {dispatchState?.last_error ?? '—'}
              </div>
            </div>
          </div>
        </div>
      )}

      {!isCompactReady && (
        <div className="divide-y">
          {hostEntries.length === 0 && (
            <div className="px-4 py-8 text-center text-xs text-gray-400">
              未解析出主机
            </div>
          )}
          {hostEntries.map(([hostId, state]) => {
            const totalScripts = state.scripts.length;
            const matchedScripts = state.scripts.filter((s) => s.ok).length;
            return (
              <div
                key={hostId}
                data-testid={`dispatch-gate-host-${hostId}`}
                className="px-4 py-3"
              >
                <div className="flex flex-wrap items-center gap-3">
                  <span className="font-mono text-sm font-semibold text-gray-700">
                    {hostId}
                  </span>
                  <StatusBadge
                    kind="precheck-host"
                    status={state.status}
                    size="sm"
                    spin={state.status === 'syncing'}
                  />
                  {totalScripts > 0 && (
                    <span className="text-xs text-gray-500">
                      {matchedScripts}/{totalScripts} 脚本一致
                    </span>
                  )}
                  {state.sync_attempts > 0 && (
                    <span className="text-xs text-gray-400">
                      sync ×{state.sync_attempts}
                      {precheck.sync_max_attempts != null
                        ? `/${precheck.sync_max_attempts}`
                        : ''}
                    </span>
                  )}
                  {state.error && (
                    <span className="ml-auto text-xs text-red-600">
                      {state.error}
                    </span>
                  )}
                </div>

                {totalScripts > 0 && (
                  <div className="mt-2 grid grid-cols-1 gap-1 text-[11px] sm:grid-cols-2 lg:grid-cols-3">
                    {state.scripts.map((s, idx) => (
                      <div
                        key={`${s.name}-${s.version}-${idx}`}
                        className={`flex items-center justify-between gap-2 rounded px-2 py-1 ${
                          s.ok ? 'bg-green-50 text-green-800' : 'bg-red-50 text-red-700'
                        }`}
                      >
                        <span className="truncate font-mono">
                          {s.name}@{s.version}
                        </span>
                        <span className="shrink-0 font-mono text-[10px] text-gray-500">
                          {s.ok ? '✓ ' : '✗ '}
                          {shortSha(s.actual_sha)}
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
