import { ArrowDown, ArrowUp, ArrowUpDown } from 'lucide-react';
import { StatusBadge } from '@/components/ui/status-badge';
import { PaginationBar } from '@/components/ui/pagination-bar';
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import { TEXT } from '@/design-system/tokens';
import { cn } from '@/lib/utils';
import type { HostActiveJob } from '@/utils/api';
import type { ReadinessDevice } from '@/utils/planExecuteReadiness';
import {
  toggleDeviceTableSort,
  type DeviceTableSort,
} from './planExecuteTableSort';
import { isSchedulable } from './tileStatus';

interface DeviceTablePanelProps {
  devices: ReadinessDevice[];
  selectedIds: Set<number>;
  hostMap: Map<string, { ip?: string | null; name?: string | null }>;
  readinessByDeviceId: Map<number, { ready: boolean; reasons: string[] }>;
  pageReadinessByDeviceId: Map<number, { ready: boolean; reasons: string[] }>;
  occupancyByDeviceId: Map<number, HostActiveJob>;
  highlightId?: number | null;
  tableSort: DeviceTableSort | null;
  onTableSortChange: (next: DeviceTableSort | null) => void;
  onToggleDevice: (device: ReadinessDevice) => void;
  onOpenPlanRun: (planRunId: number) => void;
  page: number;
  totalPages: number;
  total: number;
  pageSize: number;
  canPreviousPage: boolean;
  canNextPage: boolean;
  onGoToPage: (page: number) => void;
  onNextPage: () => void;
  onPrevPage: () => void;
  onChangePageSize: (size: number) => void;
}

export function DeviceTablePanel({
  devices,
  selectedIds,
  hostMap,
  readinessByDeviceId,
  pageReadinessByDeviceId,
  occupancyByDeviceId,
  highlightId,
  tableSort,
  onTableSortChange,
  onToggleDevice,
  onOpenPlanRun,
  page,
  totalPages,
  total,
  pageSize,
  canPreviousPage,
  canNextPage,
  onGoToPage,
  onNextPage,
  onPrevPage,
  onChangePageSize,
}: DeviceTablePanelProps) {
  return (
    <div className="flex h-full min-h-0 flex-col p-3" data-testid="device-table-panel">
      <div className="min-h-0 flex-1 overflow-auto rounded-lg border">
        <table className="w-full min-w-[800px] text-sm">
          <thead className="sticky top-0 z-10 bg-muted/95 text-left text-xs">
            <tr>
              <th className="w-10 px-3 py-2" />
              {([
                ['serial', 'Serial'],
                ['host', '节点'],
                ['model', '型号'],
                ['version', '版本'],
              ] as const).map(([key, label]) => {
                const active = tableSort?.key === key;
                const Icon = !active
                  ? ArrowUpDown
                  : tableSort?.dir === 'asc'
                    ? ArrowUp
                    : ArrowDown;
                return (
                  <th key={key} className="px-3 py-2">
                    <button
                      type="button"
                      className="inline-flex items-center gap-1 font-medium hover:text-foreground"
                      onClick={() => onTableSortChange(toggleDeviceTableSort(tableSort, key))}
                      aria-label={`按${label}排序`}
                    >
                      {label}
                      <Icon
                        className={cn(
                          'h-3.5 w-3.5',
                          active ? 'text-foreground' : 'text-muted-foreground',
                        )}
                      />
                    </button>
                  </th>
                );
              })}
              <th className="px-3 py-2">状态</th>
              <th className="px-3 py-2">预检 / 占用</th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {devices.map((device) => {
              const disabled = !isSchedulable(device);
              const row =
                readinessByDeviceId.get(device.id) ?? pageReadinessByDeviceId.get(device.id);
              const occupancy = occupancyByDeviceId.get(device.id);
              const hostId = String(device.host_id ?? 'unassigned');
              const host = hostMap.get(hostId);
              const hostLabel =
                host?.ip || host?.name || (hostId === 'unassigned' ? '未分配节点' : hostId);
              const versionText = device.build_display_id || '—';
              return (
                <tr
                  key={device.id}
                  data-device-row-id={device.id}
                  className={cn(
                    disabled ? 'opacity-50' : 'cursor-pointer hover:bg-accent/50',
                    selectedIds.has(device.id) && 'bg-primary/10',
                    highlightId === device.id &&
                      'animate-pulse bg-primary/10 ring-1 ring-inset ring-primary',
                  )}
                  onClick={() => onToggleDevice(device)}
                >
                  <td className="px-3 py-2">
                    <input
                      aria-label={`选择 ${device.serial}`}
                      type="checkbox"
                      checked={selectedIds.has(device.id)}
                      disabled={disabled}
                      readOnly
                    />
                  </td>
                  <td className="px-3 py-2 font-mono text-xs">{device.serial}</td>
                  <td className="px-3 py-2 font-mono text-xs">{hostLabel}</td>
                  <td className="px-3 py-2">{device.model || '—'}</td>
                  <td className="max-w-[10rem] px-3 py-2">
                    <TooltipProvider delayDuration={200}>
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <span className="block truncate font-mono text-xs" title={versionText}>
                            {versionText}
                          </span>
                        </TooltipTrigger>
                        <TooltipContent className="max-w-sm break-all font-mono text-xs">
                          {versionText}
                        </TooltipContent>
                      </Tooltip>
                    </TooltipProvider>
                  </td>
                  <td className="px-3 py-2">
                    <StatusBadge kind="device" status={device.status} size="sm" />
                  </td>
                  <td
                    className={cn(
                      'px-3 py-2 text-xs',
                      row?.ready ? 'text-success' : row ? 'text-destructive' : TEXT.subtitle,
                    )}
                  >
                    {occupancy?.plan_run_id != null ? (
                      <a
                        href={`/execution/plan-runs/${occupancy.plan_run_id}`}
                        onClick={(event) => {
                          event.stopPropagation();
                          event.preventDefault();
                          onOpenPlanRun(occupancy.plan_run_id!);
                        }}
                        className="text-primary underline-offset-2 hover:underline"
                      >
                        执行中 · PlanRun #{occupancy.plan_run_id}
                      </a>
                    ) : row?.ready ? (
                      '就绪'
                    ) : row ? (
                      row.reasons.join('、')
                    ) : (
                      '选择后检查'
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {total > 0 && (
        <div className="mt-3 shrink-0">
          <PaginationBar
            page={page}
            totalPages={totalPages}
            total={total}
            pageSize={pageSize}
            canPreviousPage={canPreviousPage}
            canNextPage={canNextPage}
            onGoToPage={onGoToPage}
            onNextPage={onNextPage}
            onPrevPage={onPrevPage}
            onChangePageSize={onChangePageSize}
            pageSizeOptions={[20, 50, 100]}
          />
        </div>
      )}
    </div>
  );
}
