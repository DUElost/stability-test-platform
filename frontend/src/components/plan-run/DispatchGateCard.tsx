import { useMemo } from 'react';
import {
  CheckCircle2,
  XCircle,
  Loader2,
  RefreshCw,
  ShieldCheck,
  AlertTriangle,
} from 'lucide-react';
import type {
  PrecheckHostState,
  PrecheckPhase,
  PrecheckState,
} from '@/utils/api/types';

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
}

const PHASE_LABEL: Record<PrecheckPhase, { label: string; cls: string; Icon: React.ElementType }> = {
  verifying: {
    label: '校验脚本一致性',
    cls: 'bg-blue-100 text-blue-800 ring-blue-300',
    Icon: ShieldCheck,
  },
  syncing: {
    label: '同步漂移主机',
    cls: 'bg-amber-100 text-amber-800 ring-amber-300',
    Icon: RefreshCw,
  },
  reverifying: {
    label: '同步后再校验',
    cls: 'bg-blue-100 text-blue-800 ring-blue-300',
    Icon: ShieldCheck,
  },
  ready: {
    label: '门禁通过',
    cls: 'bg-green-100 text-green-800 ring-green-300',
    Icon: CheckCircle2,
  },
  failed: {
    label: '门禁失败',
    cls: 'bg-red-100 text-red-800 ring-red-300',
    Icon: XCircle,
  },
};

const HOST_STATUS_BADGE: Record<
  PrecheckHostState['status'],
  { label: string; cls: string; Icon: React.ElementType }
> = {
  pending: { label: '待检查', cls: 'text-gray-500 bg-gray-100', Icon: Loader2 },
  ok: { label: '一致', cls: 'text-green-700 bg-green-100', Icon: CheckCircle2 },
  syncing: { label: '同步中', cls: 'text-amber-700 bg-amber-100', Icon: RefreshCw },
  synced: { label: '已同步', cls: 'text-blue-700 bg-blue-100', Icon: CheckCircle2 },
  failed: { label: '失败', cls: 'text-red-700 bg-red-100', Icon: XCircle },
};

function shortSha(sha?: string | null): string {
  if (!sha) return '—';
  return sha.length <= 8 ? sha : sha.slice(0, 8) + '…';
}

function formatTimestamp(value?: string | null): string {
  if (!value) return '—';
  return value.replace('T', ' ').replace('Z', ' UTC');
}

function getReadyLabel(
  dispatchStatus?: string | null,
  isTerminal?: boolean,
): string {
  if (dispatchStatus === 'completed' || isTerminal) {
    return '门禁通过 · 派发完成';
  }
  if (dispatchStatus === 'running') {
    return '门禁通过 · 派发中';
  }
  if (dispatchStatus === 'queued') {
    return '门禁通过 · 等待派发';
  }
  return '门禁通过';
}

export default function DispatchGateCard({
  precheck,
  dispatchState,
  isTerminal,
}: Props) {
  // Don't render at all when:
  //   1) there is no precheck context (PlanRun pre-dates ADR-0021).
  if (!precheck) return null;

  const phaseCfg = PHASE_LABEL[precheck.phase];
  const phaseLabel =
    precheck.phase === 'ready'
      ? getReadyLabel(dispatchState?.status, isTerminal)
      : phaseCfg.label;
  const hostEntries = Object.entries(precheck.hosts);
  const totalHosts = hostEntries.length;
  const isCompactReady =
    !isTerminal &&
    precheck.phase === 'ready' &&
    dispatchState?.status === 'completed';

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
        <span
          className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-semibold ring-1 ring-inset ${phaseCfg.cls}`}
        >
          <phaseCfg.Icon
            className={`h-3.5 w-3.5 ${
              precheck.phase === 'syncing' || precheck.phase === 'verifying' || precheck.phase === 'reverifying'
                ? 'animate-spin'
                : ''
            }`}
          />
          {phaseLabel}
        </span>
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
      </div>

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
            const badge = HOST_STATUS_BADGE[state.status];
            const totalScripts = state.scripts.length;
            const matchedScripts = state.scripts.filter((s) => s.matched).length;
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
                  <span
                    className={`inline-flex items-center gap-1 rounded px-2 py-0.5 text-[10px] font-semibold ${badge.cls}`}
                  >
                    <badge.Icon
                      className={`h-3 w-3 ${
                        state.status === 'syncing' ? 'animate-spin' : ''
                      }`}
                    />
                    {badge.label}
                  </span>
                  {totalScripts > 0 && (
                    <span className="text-xs text-gray-500">
                      {matchedScripts}/{totalScripts} 脚本一致
                    </span>
                  )}
                  {state.sync_attempts > 0 && (
                    <span className="text-xs text-gray-400">
                      sync ×{state.sync_attempts}
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
                          s.matched ? 'bg-green-50 text-green-800' : 'bg-red-50 text-red-700'
                        }`}
                      >
                        <span className="truncate font-mono">
                          {s.name}@{s.version}
                        </span>
                        <span className="shrink-0 font-mono text-[10px] text-gray-500">
                          {s.matched ? '✓ ' : '✗ '}
                          {shortSha(s.actual_sha256)}
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
