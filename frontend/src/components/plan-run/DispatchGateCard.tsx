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
    label: '门禁通过 · 派发完成',
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

export default function DispatchGateCard({ precheck, isTerminal }: Props) {
  // Don't render at all when:
  //   1) there is no precheck context (PlanRun pre-dates ADR-0021), or
  //   2) the gate succeeded AND the PlanRun is RUNNING — once it's running
  //      operators don't need the precheck card any more, the timeline takes
  //      over.  When the PlanRun is terminal we DO keep the card visible so
  //      historical runs surface the gate outcome.
  if (!precheck) return null;
  if (precheck.phase === 'ready' && !isTerminal) return null;

  const phaseCfg = PHASE_LABEL[precheck.phase];
  const hostEntries = Object.entries(precheck.hosts);
  const totalHosts = hostEntries.length;

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
          {phaseCfg.label}
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
    </div>
  );
}
