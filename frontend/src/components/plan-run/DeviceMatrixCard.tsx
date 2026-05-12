import { useMemo, useState } from 'react';
import {
  Filter,
  CheckCircle2,
  XCircle,
  Loader2,
  Clock,
  AlertTriangle,
  PauseCircle,
  Activity,
} from 'lucide-react';
import { Skeleton } from '@/components/ui/skeleton';
import type {
  DeviceMatrixItem,
  DeviceUiStatus,
  PlanRunDevicesPayload,
} from '@/utils/api/types';

interface Props {
  data: PlanRunDevicesPayload | undefined;
  isLoading?: boolean;
  /** Filter values are lifted up so the parent can sync URL params later. */
  statusFilter?: DeviceUiStatus | 'all';
  hostFilter?: string | 'all';
  onStatusFilterChange?: (s: DeviceUiStatus | 'all') => void;
  onHostFilterChange?: (h: string | 'all') => void;
  onSelectDevice?: (device: DeviceMatrixItem) => void;
}

const STATUS_DEF: Array<{ key: DeviceUiStatus | 'all'; label: string }> = [
  { key: 'all', label: '全部' },
  { key: 'running', label: '运行中' },
  { key: 'completed', label: '完成' },
  { key: 'failed', label: '失败' },
  { key: 'risk', label: '风险' },
  { key: 'backoff', label: '退避' },
  { key: 'pending', label: '等待' },
];

const STATUS_PILL: Record<DeviceUiStatus, { cls: string; Icon: React.ElementType; label: string }> = {
  running: { cls: 'bg-orange-100 text-orange-800 ring-orange-300', Icon: Loader2, label: '运行' },
  completed: { cls: 'bg-green-100 text-green-800 ring-green-300', Icon: CheckCircle2, label: '完成' },
  failed: { cls: 'bg-red-100 text-red-800 ring-red-300', Icon: XCircle, label: '失败' },
  risk: { cls: 'bg-amber-100 text-amber-800 ring-amber-300', Icon: AlertTriangle, label: '风险' },
  backoff: { cls: 'bg-purple-100 text-purple-800 ring-purple-300', Icon: Clock, label: '退避' },
  pending: { cls: 'bg-gray-100 text-gray-700 ring-gray-300', Icon: PauseCircle, label: '等待' },
};

function fmtRelative(ts: string | null | undefined, now = Date.now()): string {
  if (!ts) return '—';
  const t = new Date(ts).getTime();
  if (Number.isNaN(t)) return '—';
  const diff = (t - now) / 1000;
  if (diff > 0) {
    if (diff < 60) return `${Math.round(diff)}s 后`;
    if (diff < 3600) return `${Math.round(diff / 60)}m 后`;
    return `${Math.round(diff / 3600)}h 后`;
  }
  const past = -diff;
  if (past < 60) return `${Math.round(past)}s 前`;
  if (past < 3600) return `${Math.round(past / 60)}m 前`;
  return `${Math.round(past / 3600)}h 前`;
}

export default function DeviceMatrixCard({
  data,
  isLoading = false,
  statusFilter = 'all',
  hostFilter = 'all',
  onStatusFilterChange,
  onHostFilterChange,
  onSelectDevice,
}: Props) {
  const [view, setView] = useState<'grid' | 'table'>('table');

  const total = data?.total ?? 0;
  const devices = data?.devices ?? [];
  const byStatus = data?.by_status ?? { all: 0 };
  const byHost = data?.by_host ?? {};

  const hosts = useMemo(
    () => Object.keys(byHost).sort((a, b) => a.localeCompare(b)),
    [byHost],
  );

  return (
    <section data-testid="device-matrix" className="space-y-2">
      <div className="mx-1 flex items-center gap-2.5">
        <span className="h-3 w-1 rounded-sm bg-gradient-to-b from-blue-600 to-blue-400" />
        <span className="text-xs font-bold uppercase tracking-wider text-gray-700">
          设备总览
        </span>
        <span className="text-[11px] text-gray-500">
          {total} 设备 · {hosts.length} Host
        </span>
        <div className="ml-auto flex items-center gap-1 rounded-md border bg-white p-0.5 text-[11px]">
          <button
            type="button"
            data-testid="device-view-table"
            onClick={() => setView('table')}
            className={`rounded px-2 py-0.5 ${
              view === 'table' ? 'bg-blue-100 text-blue-700' : 'text-gray-500'
            }`}
          >
            表格
          </button>
          <button
            type="button"
            data-testid="device-view-grid"
            onClick={() => setView('grid')}
            className={`rounded px-2 py-0.5 ${
              view === 'grid' ? 'bg-blue-100 text-blue-700' : 'text-gray-500'
            }`}
          >
            缩略图
          </button>
        </div>
      </div>

      <div className="overflow-hidden rounded-xl border bg-white shadow-sm">
        {/* Filter bar */}
        <div className="flex flex-wrap items-center gap-1 border-b bg-white px-3 py-2">
          <Filter className="mr-1 h-3 w-3 text-gray-400" />
          <span className="mr-1 text-[10px] font-semibold uppercase tracking-wider text-gray-400">
            状态
          </span>
          {STATUS_DEF.map((d) => (
            <button
              key={d.key}
              type="button"
              data-testid={`device-status-filter-${d.key}`}
              onClick={() => onStatusFilterChange?.(d.key)}
              className={`rounded-md px-2 py-0.5 text-[11px] transition ${
                statusFilter === d.key
                  ? 'bg-blue-100 font-semibold text-blue-700'
                  : 'text-gray-600 hover:bg-gray-100'
              }`}
            >
              {d.label}
              <span className="ml-1 text-[10px] text-gray-400">
                {byStatus[d.key] ?? 0}
              </span>
            </button>
          ))}
          {hosts.length > 0 && (
            <>
              <span className="mx-2 h-3 w-px bg-gray-200" />
              <span className="mr-1 text-[10px] font-semibold uppercase tracking-wider text-gray-400">
                Host
              </span>
              <select
                data-testid="device-host-filter"
                value={hostFilter}
                onChange={(e) => onHostFilterChange?.(e.target.value)}
                className="rounded-md border px-2 py-0.5 text-[11px] focus:outline-none focus:ring-2 focus:ring-blue-500/20"
              >
                <option value="all">全部 ({total})</option>
                {hosts.map((h) => (
                  <option key={h} value={h}>
                    {h} ({byHost[h] ?? 0})
                  </option>
                ))}
              </select>
            </>
          )}
        </div>

        {/* Body */}
        {isLoading && devices.length === 0 ? (
          <div className="space-y-2 p-3">
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-full" />
          </div>
        ) : devices.length === 0 ? (
          <div className="py-10 text-center text-xs text-gray-400">
            <Activity className="mx-auto mb-2 h-5 w-5 opacity-30" />
            该过滤条件下暂无设备
          </div>
        ) : view === 'table' ? (
          <DeviceTable devices={devices} onSelect={onSelectDevice} />
        ) : (
          <DeviceGrid devices={devices} onSelect={onSelectDevice} />
        )}
      </div>
    </section>
  );
}

// ── Table view ───────────────────────────────────────────────────────────

function DeviceTable({
  devices,
  onSelect,
}: {
  devices: DeviceMatrixItem[];
  onSelect?: (d: DeviceMatrixItem) => void;
}) {
  const now = Date.now();
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[12px]">
        <thead className="bg-gray-50 text-[10.5px] font-semibold uppercase tracking-wider text-gray-500">
          <tr>
            <th className="px-3 py-2 text-left">Serial</th>
            <th className="px-2 py-2 text-left">Host</th>
            <th className="px-2 py-2 text-left">状态</th>
            <th className="px-2 py-2 text-left">阶段</th>
            <th className="px-2 py-2 text-left">当前步骤</th>
            <th className="px-2 py-2 text-right">巡检周期</th>
            <th className="px-2 py-2 text-right">连击</th>
            <th className="px-2 py-2 text-right">下次重试</th>
            <th className="px-2 py-2 text-right">异常</th>
          </tr>
        </thead>
        <tbody>
          {devices.map((d) => {
            const cfg = STATUS_PILL[d.ui_status];
            const Icon = cfg.Icon;
            const failureClass =
              d.current_failure_streak >= 3
                ? 'text-red-600 font-semibold'
                : d.current_failure_streak >= 1
                ? 'text-amber-600'
                : 'text-gray-400';
            return (
              <tr
                key={d.job_id}
                data-testid={`device-row-${d.job_id}`}
                onClick={() => onSelect?.(d)}
                className="cursor-pointer border-t hover:bg-blue-50/50"
              >
                <td className="px-3 py-2 font-mono text-[11.5px]">
                  {d.device_serial || `Device #${d.device_id}`}
                </td>
                <td className="px-2 py-2 font-mono text-[11px] text-gray-500">
                  {d.host_id || '—'}
                </td>
                <td className="px-2 py-2">
                  <span
                    className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10.5px] font-semibold ring-1 ring-inset ${cfg.cls}`}
                  >
                    <Icon
                      className={`h-3 w-3 ${
                        d.ui_status === 'running' ? 'animate-spin' : ''
                      }`}
                    />
                    {cfg.label}
                  </span>
                </td>
                <td className="px-2 py-2 text-[11px] uppercase text-gray-600">
                  {d.current_stage}
                </td>
                <td className="px-2 py-2 font-mono text-[11px] text-gray-700">
                  {d.current_step || '—'}
                </td>
                <td className="px-2 py-2 text-right font-mono text-[11px] text-gray-700">
                  #{d.patrol_cycle_count}
                  <span className="ml-1 text-[10px] text-gray-400">
                    ({d.patrol_success_cycle_count}✓ / {d.patrol_failed_cycle_count}✗)
                  </span>
                </td>
                <td className={`px-2 py-2 text-right font-mono text-[11px] ${failureClass}`}>
                  {d.current_failure_streak > 0
                    ? `× ${d.current_failure_streak}`
                    : '—'}
                </td>
                <td className="px-2 py-2 text-right text-[11px] text-gray-500">
                  {d.next_retry_at
                    ? fmtRelative(d.next_retry_at, now)
                    : d.manual_action === 'EXIT_REQUESTED'
                    ? '退出待执行'
                    : d.manual_action === 'RETRY_NOW'
                    ? '已请求立即重试'
                    : '—'}
                </td>
                <td className="px-2 py-2 text-right text-[11px] text-gray-700">
                  {d.log_signal_count > 0 ? (
                    <span className="text-amber-700">⚠ {d.log_signal_count}</span>
                  ) : (
                    '—'
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ── Grid (mini-map) view ──────────────────────────────────────────────────

// ── Minimap (缩略图) — prototype v8 design ─────────────────────────────

const MINIMAP_CELL_CLS: Record<DeviceUiStatus, string> = {
  completed:
    'bg-green-400/90 hover:bg-green-500',
  running:
    'bg-orange-500 bg-[linear-gradient(45deg,rgba(255,255,255,.35)_25%,transparent_25%,transparent_50%,rgba(255,255,255,.35)_50%,rgba(255,255,255,.35)_75%,transparent_75%)] bg-[length:8px_8px] [animation:dev-stripe_1s_linear_infinite]',
  failed:
    'bg-red-500/90 hover:bg-red-600',
  risk:
    'bg-amber-400/90 hover:bg-amber-500',
  backoff:
    'bg-purple-400/80 hover:bg-purple-500',
  pending:
    'bg-gray-300 hover:bg-gray-400',
};

const CELL_LABEL: Record<DeviceUiStatus, string> = {
  running: '运行中',
  completed: '完成',
  failed: '失败',
  risk: '风险',
  backoff: '退避',
  pending: '等待',
};

function DeviceGrid({
  devices,
  onSelect,
}: {
  devices: DeviceMatrixItem[];
  onSelect?: (d: DeviceMatrixItem) => void;
}) {
  return (
    <div className="p-3">
      <style>{`
        @keyframes dev-stripe {
          from { background-position: 0 0; }
          to   { background-position: 8px 0; }
        }
      `}</style>
      <div className="grid gap-1.5" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(24px, 1fr))' }}>
        {devices.map((d) => {
          const cellCls = MINIMAP_CELL_CLS[d.ui_status] ?? 'bg-gray-300';
          const label = CELL_LABEL[d.ui_status] ?? d.ui_status;
          const serial = d.device_serial || `D${d.device_id}`;
          return (
            <button
              key={d.job_id}
              type="button"
              data-testid={`device-cell-${d.job_id}`}
              onClick={() => onSelect?.(d)}
              title={`${serial} · ${label}${d.host_id ? ` · ${d.host_id}` : ''}`}
              className={`relative aspect-square w-full rounded-sm ring-1 ring-inset ring-black/10 transition-all hover:z-10 hover:scale-125 hover:shadow-lg hover:ring-2 hover:ring-blue-500 ${cellCls}`}
            />
          );
        })}
      </div>
    </div>
  );
}
