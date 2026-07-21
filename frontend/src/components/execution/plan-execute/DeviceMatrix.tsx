import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { useVirtualizer } from '@tanstack/react-virtual';
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
import { rangeSelectIds, sortDevicesStable } from './planExecuteSelection';
import { isSchedulable, resolveDeviceTileStatus } from './tileStatus';
import type { DeviceTileStatus } from './types';

const TILE_CLS: Record<DeviceTileStatus, string> = {
  ready: 'border-transparent bg-success/75',
  blocked: 'border-transparent bg-warning/55',
  busy: 'border-transparent bg-primary/55',
  offline: 'border-transparent bg-muted-foreground/25',
};

const TILE_PX = 32;
const TILE_GAP = 4;
const BAND_ROW_H = 28;
const TILE_ROW_H = TILE_PX + TILE_GAP;

type VirtualRow =
  | { type: 'band'; hostId: string; label: string; total: number; selected: number }
  | { type: 'tiles'; devices: ReadinessDevice[] };

interface DeviceMatrixProps {
  devices: ReadinessDevice[];
  selectedIds: Set<number>;
  hostMap: Map<string, { ip?: string | null; name?: string | null }>;
  readinessByDeviceId: Map<number, { ready: boolean; reasons: string[] }>;
  occupancyByDeviceId: Map<number, HostActiveJob>;
  highlightId?: number | null;
  onToggle: (device: ReadinessDevice, event: { shiftKey: boolean }) => void;
  lastClickedIndexRef: React.MutableRefObject<number | null>;
  /** 填满父级高度（选机工作台舞台）；默认仍可独立使用。 */
  className?: string;
}

export function buildMatrixVirtualRows(
  ordered: ReadinessDevice[],
  hostMap: Map<string, { ip?: string | null; name?: string | null }>,
  selectedIds: Set<number>,
  cols: number,
): VirtualRow[] {
  const safeCols = Math.max(1, cols);
  const bands = new Map<string, { hostId: string; label: string; items: ReadinessDevice[] }>();
  for (const device of ordered) {
    const hostId = String(device.host_id ?? 'unassigned');
    const host = hostMap.get(hostId);
    const label = host?.ip || host?.name || (hostId === 'unassigned' ? '未分配节点' : hostId);
    const band = bands.get(hostId) ?? { hostId, label, items: [] };
    band.items.push(device);
    bands.set(hostId, band);
  }

  const rows: VirtualRow[] = [];
  for (const band of bands.values()) {
    rows.push({
      type: 'band',
      hostId: band.hostId,
      label: band.label,
      total: band.items.length,
      selected: band.items.filter((d) => selectedIds.has(d.id)).length,
    });
    for (let i = 0; i < band.items.length; i += safeCols) {
      rows.push({ type: 'tiles', devices: band.items.slice(i, i + safeCols) });
    }
  }
  return rows;
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
  className,
}: DeviceMatrixProps) {
  const ordered = useMemo(() => sortDevicesStable(devices, hostMap), [devices, hostMap]);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const [cols, setCols] = useState(16);

  useLayoutEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const measure = () => {
      const width = el.clientWidth - 24; // p-3
      const next = Math.max(1, Math.floor((width + TILE_GAP) / (TILE_PX + TILE_GAP)));
      setCols((prev) => (prev === next ? prev : next));
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const rows = useMemo(
    () => buildMatrixVirtualRows(ordered, hostMap, selectedIds, cols),
    [ordered, hostMap, selectedIds, cols],
  );

  const indexById = useMemo(() => {
    const map = new Map<number, number>();
    ordered.forEach((d, i) => map.set(d.id, i));
    return map;
  }, [ordered]);

  const rowVirtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: (index) => (rows[index]?.type === 'band' ? BAND_ROW_H : TILE_ROW_H),
    overscan: 8,
  });

  useEffect(() => {
    if (highlightId == null) return;
    const rowIndex = rows.findIndex(
      (row) => row.type === 'tiles' && row.devices.some((d) => d.id === highlightId),
    );
    if (rowIndex >= 0) {
      rowVirtualizer.scrollToIndex(rowIndex, { align: 'center' });
    }
    const el = document.querySelector(`[data-matrix-device-id="${highlightId}"]`);
    el?.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }, [highlightId, rows, rowVirtualizer]);

  if (ordered.length === 0) {
    return (
      <div className={cn('flex min-h-40 items-center justify-center text-sm', TEXT.subtitle)}>
        当前筛选无设备
      </div>
    );
  }

  return (
    <TooltipProvider delayDuration={200}>
      <div
        className={cn('flex h-full min-h-[280px] flex-col', className)}
        data-testid="device-matrix"
      >
        <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto p-3">
          <div
            className="relative w-full"
            style={{ height: `${rowVirtualizer.getTotalSize()}px` }}
          >
            {rowVirtualizer.getVirtualItems().map((virtualRow) => {
              const row = rows[virtualRow.index];
              if (!row) return null;
              return (
                <div
                  key={virtualRow.key}
                  data-index={virtualRow.index}
                  ref={rowVirtualizer.measureElement}
                  className="absolute left-0 top-0 w-full"
                  style={{ transform: `translateY(${virtualRow.start}px)` }}
                >
                  {row.type === 'band' ? (
                    <div className={cn('flex flex-wrap items-center justify-between gap-2 pb-1 text-xs', TEXT.subtitle)}>
                      <strong className="text-sm font-medium text-foreground">{row.label}</strong>
                      <span>
                        {row.total} 台 · 已选 {row.selected}
                      </span>
                    </div>
                  ) : (
                    <div
                      className="pb-1"
                      style={{
                        display: 'grid',
                        gridTemplateColumns: `repeat(${cols}, ${TILE_PX}px)`,
                        gap: TILE_GAP,
                      }}
                    >
                      {row.devices.map((device) => {
                        const readiness = readinessByDeviceId.get(device.id);
                        const occupancy = occupancyByDeviceId.get(device.id);
                        const status = resolveDeviceTileStatus(device, {
                          readinessReady: readiness?.ready,
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
                                  'relative aspect-square overflow-hidden rounded-[5px] border-2 transition-transform hover:z-10 hover:scale-110 hover:shadow-md',
                                  TILE_CLS[status],
                                  selected && 'border-foreground shadow-[inset_0_0_0_1px_#fff]',
                                  flash && 'animate-pulse outline outline-2 outline-offset-1 outline-primary',
                                  !canSelect && 'cursor-not-allowed opacity-60',
                                )}
                              >
                                {status === 'blocked' && (
                                  <span
                                    aria-hidden
                                    className="pointer-events-none absolute inset-0"
                                    style={{
                                      backgroundImage:
                                        'repeating-linear-gradient(-45deg, hsl(38 92% 50% / 0.55), hsl(38 92% 50% / 0.55) 3px, hsl(38 92% 70% / 0.35) 3px, hsl(38 92% 70% / 0.35) 6px)',
                                    }}
                                  />
                                )}
                                {selected && (
                                  <span
                                    className="absolute right-0.5 top-0.5 h-1.5 w-1.5 rounded-full bg-white shadow-[0_0_0_1px_hsl(222_84%_15%)]"
                                    aria-hidden
                                  />
                                )}
                              </button>
                            </TooltipTrigger>
                            <TooltipContent side="top" className="max-w-xs space-y-0.5 bg-popover text-popover-foreground">
                              <div className="font-mono text-xs font-medium">{device.serial}</div>
                              <div className="text-[11px]">节点：{hostLabel}</div>
                              <div className="text-[11px]">型号：{device.model || '—'}</div>
                              <div className="text-[11px]">版本：{device.build_display_id || '—'}</div>
                              <div className="text-[11px]">状态：{status}</div>
                              {status === 'blocked' && readiness?.reasons?.[0] ? (
                                <div className="text-[11px] text-destructive">阻塞：{readiness.reasons[0]}</div>
                              ) : null}
                            </TooltipContent>
                          </Tooltip>
                        );
                      })}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
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
