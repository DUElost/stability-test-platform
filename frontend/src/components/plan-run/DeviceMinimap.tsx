import {
  Filter,
} from 'lucide-react';
import type {
  DeviceMatrixItem,
  DeviceUiStatus,
  PlanRunDevicesPayload,
} from '@/utils/api/types';

interface Props {
  data: PlanRunDevicesPayload | undefined;
  isLoading?: boolean;
  statusFilter?: DeviceUiStatus | 'all';
  hostFilter?: string | 'all';
  onStatusFilterChange?: (s: DeviceUiStatus | 'all') => void;
  onHostFilterChange?: (h: string | 'all') => void;
  onSelectDevice?: (device: DeviceMatrixItem) => void;
}

const STATUS_FILTERS: Array<{ key: DeviceUiStatus | 'all'; label: string }> = [
  { key: 'all', label: '全部' },
  { key: 'running', label: '运行' },
  { key: 'completed', label: '完成' },
  { key: 'unknown', label: '失联' },
  { key: 'failed', label: '失败' },
  { key: 'risk', label: '风险' },
  { key: 'backoff', label: '退避' },
  { key: 'pending', label: '等待' },
];

const MINIMAP_CELL_CLS: Record<DeviceUiStatus, string> = {
  completed: 'bg-green-400/90 hover:bg-green-500',
  running:
    'bg-orange-500 bg-[linear-gradient(45deg,rgba(255,255,255,.35)_25%,transparent_25%,transparent_50%,rgba(255,255,255,.35)_50%,rgba(255,255,255,.35)_75%,transparent_75%)] bg-[length:8px_8px] [animation:dev-stripe_1s_linear_infinite]',
  unknown: 'bg-purple-500/90 hover:bg-purple-600',
  failed: 'bg-red-500/90 hover:bg-red-600',
  risk: 'bg-amber-400/90 hover:bg-amber-500',
  backoff: 'bg-purple-400/80 hover:bg-purple-500',
  pending: 'bg-gray-300 hover:bg-gray-400',
};

const CELL_LABEL: Record<DeviceUiStatus, string> = {
  running: '运行中',
  completed: '完成',
  unknown: '失联',
  failed: '失败',
  risk: '风险',
  backoff: '退避',
  pending: '等待',
};

export default function DeviceMinimap({
  data,
  isLoading = false,
  statusFilter = 'all',
  hostFilter = 'all',
  onStatusFilterChange,
  onHostFilterChange,
  onSelectDevice,
}: Props) {
  const total = data?.total ?? 0;
  const devices = data?.devices ?? [];
  const byStatus = data?.by_status ?? { all: 0 };
  const byHost = data?.by_host ?? {};
  const hosts = Object.keys(byHost).sort((a, b) => a.localeCompare(b));

  return (
    <section data-testid="device-minimap" className="space-y-2">
      <div className="mx-1 flex items-center gap-2.5">
        <span className="h-3 w-1 rounded-sm bg-gradient-to-b from-blue-600 to-blue-400" />
        <span className="text-xs font-bold uppercase tracking-wider text-gray-700">
          设备缩略图
        </span>
        <span className="text-[11px] text-gray-500">{total} 设备 · {hosts.length} Host</span>
      </div>

      <div className="overflow-hidden rounded-xl border bg-white shadow-sm">
        {/* Filter bar */}
        <div className="flex flex-wrap items-center gap-1 border-b bg-white px-3 py-2">
          <Filter className="mr-1 h-3 w-3 text-gray-400" />
          {STATUS_FILTERS.map((d) => (
            <button
              key={d.key}
              type="button"
              data-testid={`minimap-status-filter-${d.key}`}
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
              <select
                data-testid="minimap-host-filter"
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

        {/* Grid body */}
        {isLoading && devices.length === 0 ? (
          <div className="flex items-center justify-center py-8 text-xs text-gray-400">
            加载中…
          </div>
        ) : devices.length === 0 ? (
          <div className="py-8 text-center text-xs text-gray-400">
            该过滤条件下暂无设备
          </div>
        ) : (
          <div className="p-3">
            <style>{`
              @keyframes dev-stripe {
                from { background-position: 0 0; }
                to   { background-position: 8px 0; }
              }
            `}</style>
            <div
              className="grid gap-1"
              style={{
                gridTemplateColumns: 'repeat(auto-fill, minmax(24px, 1fr))',
              }}
            >
              {devices.map((d) => (
                <button
                  key={d.job_id}
                  type="button"
                  data-testid={`minimap-cell-${d.job_id}`}
                  onClick={() => onSelectDevice?.(d)}
                  title={`${d.device_serial || `Device #${d.device_id}`} — ${CELL_LABEL[d.ui_status]}`}
                  className={`aspect-square rounded-sm border border-transparent transition-transform hover:scale-[1.12] hover:shadow-[0_0_0_2px_rgba(59,130,246,0.45)] hover:z-10 ${MINIMAP_CELL_CLS[d.ui_status]}`}
                />
              ))}
            </div>
          </div>
        )}
      </div>
    </section>
  );
}
