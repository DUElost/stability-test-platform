import { useEffect, useMemo } from 'react';
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
import { X } from 'lucide-react';
import { rangeSelectIds, sortDevicesStable } from './planExecuteSelection';
import { isSchedulable, resolveDeviceTileStatus } from './tileStatus';
import type { DeviceTileStatus } from './types';

const TILE_CLS: Record<DeviceTileStatus, string> = {
  ready: 'bg-success/80 border-success',
  blocked: 'border-warning bg-warning/70',
  busy: 'bg-primary/55 border-primary',
  offline: 'bg-muted border-muted-foreground/30',
};

interface DeviceMatrixProps {
  devices: ReadinessDevice[];
  selectedIds: Set<number>;
  hostMap: Map<string, { ip?: string | null; name?: string | null }>;
  readinessByDeviceId: Map<number, { ready: boolean; reasons: string[] }>;
  occupancyByDeviceId: Map<number, HostActiveJob>;
  highlightId?: number | null;
  onToggle: (device: ReadinessDevice, event: { shiftKey: boolean }) => void;
  lastClickedIndexRef: React.MutableRefObject<number | null>;
}

export function DeviceMatrix({
  devices,
  selectedIds,
  hostMap,
  readinessByDeviceId,
  occupancyByDeviceId,
  highlightId,
  onToggle,
  lastClickedIndexRef,
}: DeviceMatrixProps) {
  const ordered = useMemo(() => sortDevicesStable(devices, hostMap), [devices, hostMap]);
  const bands = useMemo(() => {
    const map = new Map<string, { hostId: string; label: string; items: ReadinessDevice[] }>();
    for (const device of ordered) {
      const hostId = String(device.host_id ?? 'unassigned');
      const host = hostMap.get(hostId);
      const label = host?.ip || host?.name || (hostId === 'unassigned' ? '未分配节点' : hostId);
      const band = map.get(hostId) ?? { hostId, label, items: [] };
      band.items.push(device);
      map.set(hostId, band);
    }
    return Array.from(map.values());
  }, [ordered, hostMap]);

  const indexById = useMemo(() => {
    const map = new Map<number, number>();
    ordered.forEach((d, i) => map.set(d.id, i));
    return map;
  }, [ordered]);

  useEffect(() => {
    if (highlightId == null) return;
    const el = document.querySelector(`[data-matrix-device-id="${highlightId}"]`);
    el?.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }, [highlightId]);

  if (ordered.length === 0) {
    return (
      <div className={cn('flex min-h-40 items-center justify-center text-sm', TEXT.subtitle)}>
        当前筛选无设备
      </div>
    );
  }

  return (
    <TooltipProvider delayDuration={200}>
      <div className="max-h-[min(70vh,720px)] space-y-4 overflow-y-auto p-3" data-testid="device-matrix">
        <div className="flex flex-wrap gap-3 text-xs">
          <span className="inline-flex items-center gap-1"><i className="inline-block h-2.5 w-2.5 rounded-sm bg-success/80" />就绪</span>
          <span className="inline-flex items-center gap-1">
            <i
              className="inline-block h-2.5 w-2.5 rounded-sm border border-warning bg-warning/70"
              style={{ backgroundImage: 'repeating-linear-gradient(45deg, transparent, transparent 1px, rgba(255,255,255,0.55) 1px, rgba(255,255,255,0.55) 2px)' }}
            />
            阻塞
          </span>
          <span className="inline-flex items-center gap-1"><i className="inline-block h-2.5 w-2.5 rounded-sm bg-primary/55" />占用</span>
          <span className="inline-flex items-center gap-1"><i className="inline-block h-2.5 w-2.5 rounded-sm bg-muted" />离线</span>
          <span className={TEXT.subtitle}>点击切换 · Shift 连选</span>
        </div>
        {bands.map((band) => (
          <section key={band.hostId} className="space-y-2">
            <div className={cn('flex flex-wrap items-center justify-between gap-2 text-xs', TEXT.subtitle)}>
              <strong className="text-sm font-medium text-foreground">{band.label}</strong>
              <span>
                {band.items.length} 台 · 已选 {band.items.filter((d) => selectedIds.has(d.id)).length}
              </span>
            </div>
            <div className="flex flex-wrap gap-1">
              {band.items.map((device) => {
                const row = readinessByDeviceId.get(device.id);
                const occupancy = occupancyByDeviceId.get(device.id);
                const status = resolveDeviceTileStatus(device, {
                  readinessReady: row?.ready,
                  occupancy,
                });
                const selected = selectedIds.has(device.id);
                const canSelect = isSchedulable(device);
                const host = hostMap.get(String(device.host_id ?? 'unassigned'));
                const hostLabel = host?.ip || host?.name || device.host_id || '节点未知';
                const flash = highlightId === device.id;
                return (
                  <Tooltip key={device.id}>
                    <TooltipTrigger asChild>
                      <button
                        type="button"
                        role="checkbox"
                        aria-checked={selected}
                        aria-label={device.serial}
                        data-matrix-device-id={device.id}
                        disabled={!canSelect}
                        onClick={(event) => {
                          if (!canSelect) return;
                          const idx = indexById.get(device.id) ?? 0;
                          onToggle(device, { shiftKey: event.shiftKey });
                          lastClickedIndexRef.current = idx;
                        }}
                        className={cn(
                          'relative h-8 w-8 shrink-0 overflow-hidden rounded-sm border transition-transform hover:z-10 hover:scale-110',
                          TILE_CLS[status],
                          selected && 'ring-2 ring-foreground ring-offset-1 ring-offset-background',
                          flash && 'animate-pulse ring-2 ring-primary',
                          !canSelect && 'cursor-not-allowed opacity-60',
                        )}
                      >
                        {status === 'blocked' && (
                          <>
                            <span
                              aria-hidden
                              className="pointer-events-none absolute inset-0 opacity-70"
                              style={{
                                backgroundImage:
                                  'repeating-linear-gradient(45deg, transparent, transparent 2px, rgba(255,255,255,0.45) 2px, rgba(255,255,255,0.45) 4px)',
                              }}
                            />
                            <X className="relative mx-auto h-3 w-3 text-primary-foreground drop-shadow" strokeWidth={3} aria-hidden />
                          </>
                        )}
                        {selected && (
                          <span className="absolute right-0.5 top-0.5 h-1.5 w-1.5 rounded-full bg-foreground" aria-hidden />
                        )}
                      </button>
                    </TooltipTrigger>
                    <TooltipContent side="top" className="max-w-xs space-y-0.5 bg-popover text-popover-foreground">
                      <div className="font-mono text-xs font-medium">{device.serial}</div>
                      <div className="text-[11px]">节点：{hostLabel}</div>
                      <div className="text-[11px]">型号：{device.model || '—'}</div>
                      <div className="text-[11px]">版本：{device.build_display_id || '—'}</div>
                      <div className="text-[11px]">状态：{status}</div>
                      {status === 'blocked' && row?.reasons?.[0] ? (
                        <div className="text-[11px] text-destructive">阻塞：{row.reasons[0]}</div>
                      ) : null}
                    </TooltipContent>
                  </Tooltip>
                );
              })}
            </div>
          </section>
        ))}
      </div>
    </TooltipProvider>
  );
}

export function applyMatrixSelection(
  ordered: ReadinessDevice[],
  prev: Set<number>,
  device: ReadinessDevice,
  event: { shiftKey: boolean },
  lastClickedIndex: number | null,
): Set<number> {
  const index = ordered.findIndex((d) => d.id === device.id);
  if (index < 0) return prev;
  if (event.shiftKey && lastClickedIndex != null) {
    const ids = rangeSelectIds(ordered, lastClickedIndex, index).filter((id) => {
      const d = ordered.find((x) => x.id === id);
      return d ? isSchedulable(d) : false;
    });
    const next = new Set(prev);
    ids.forEach((id) => next.add(id));
    return next;
  }
  const next = new Set(prev);
  if (next.has(device.id)) next.delete(device.id);
  else next.add(device.id);
  return next;
}
