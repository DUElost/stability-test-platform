import { useLayoutEffect, useRef, useState } from 'react';
import { useVirtualizer } from '@tanstack/react-virtual';
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import { Button } from '@/components/ui/button';
import { TEXT } from '@/design-system/tokens';
import { cn } from '@/lib/utils';
import { Copy, Download, X } from 'lucide-react';
import type { ReadinessDevice } from '@/utils/planExecuteReadiness';

/** 超过此数量时启用虚拟行滚动（右栏大选集）。 */
export const MINIMAP_VIRTUAL_THRESHOLD = 80;

interface SelectedMinimapProps {
  devices: ReadinessDevice[];
  readinessByDeviceId: Map<number, { ready: boolean; reasons: string[] }>;
  hostMap: Map<string, { ip?: string | null; name?: string | null }>;
  highlightId?: number | null;
  /** 嵌入右栏时去掉外框与大标题，工具条下沉到网格下方。 */
  embedded?: boolean;
  onLocate: (deviceId: number) => void;
  onRemove: (deviceId: number) => void;
  onCopySerials: () => void;
  onDownloadCsv: () => void;
}

const BLOCKED_STRIPE_CLS =
  'bg-[repeating-linear-gradient(45deg,transparent,transparent_2px,hsl(var(--foreground)/0.28)_2px,hsl(var(--foreground)/0.28)_4px)]';

function MinimapLegend({ compact }: { compact?: boolean }) {
  return (
    <div className={cn('flex flex-wrap gap-3', compact ? 'text-[11px]' : 'text-xs')}>
      <span><i className="mr-1 inline-block h-2.5 w-2.5 rounded-sm bg-success" />已选就绪</span>
      <span className="inline-flex items-center gap-1">
        <i className={cn('inline-block h-2.5 w-2.5 rounded-sm border border-destructive bg-destructive', BLOCKED_STRIPE_CLS)} />
        已选阻塞（斜纹）
      </span>
    </div>
  );
}

function MinimapTile({
  device,
  blocked,
  hostLabel,
  flash,
  row,
  onLocate,
  onRemove,
}: {
  device: ReadinessDevice;
  blocked: boolean;
  hostLabel: string;
  flash: boolean;
  row?: { ready: boolean; reasons: string[] };
  onLocate: (id: number) => void;
  onRemove: (id: number) => void;
}) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <div className="group relative aspect-square">
          <button
            type="button"
            data-minimap-device-id={device.id}
            aria-label={`定位已选设备 ${device.id}${blocked ? ' 阻塞' : ''}`}
            onClick={() => onLocate(device.id)}
            className={cn(
              'flex h-full w-full items-center justify-center overflow-hidden rounded-sm border transition-transform hover:z-10 hover:scale-110 hover:ring-2 hover:ring-primary/40',
              blocked ? 'border-destructive bg-destructive' : 'border-success bg-success',
              flash && 'animate-pulse ring-2 ring-primary',
            )}
          >
            {blocked && (
              <span
                aria-hidden
                className={cn('pointer-events-none absolute inset-0 overflow-hidden rounded-sm opacity-70', BLOCKED_STRIPE_CLS)}
              />
            )}
          </button>
          <button
            type="button"
            aria-label={`移除已选设备 ${device.id}`}
            className="absolute -right-1 -top-1 z-20 flex h-3.5 w-3.5 items-center justify-center rounded-full bg-destructive text-primary-foreground opacity-0 shadow transition-opacity group-hover:opacity-100 focus-visible:opacity-100"
            onClick={(event) => {
              event.preventDefault();
              event.stopPropagation();
              onRemove(device.id);
            }}
          >
            <X className="h-2.5 w-2.5" strokeWidth={3} aria-hidden />
          </button>
        </div>
      </TooltipTrigger>
      <TooltipContent side="top" className="max-w-xs space-y-0.5 bg-popover text-popover-foreground">
        <div className="font-mono text-xs font-medium">{device.serial}</div>
        <div className="text-[11px]">节点：{hostLabel}</div>
        <div className="text-[11px]">型号：{device.model || '—'}</div>
        <div className="text-[11px]">版本：{device.build_display_id || '—'}</div>
        {blocked && row?.reasons?.[0] ? (
          <div className="text-[11px] text-destructive">阻塞：{row.reasons[0]}</div>
        ) : null}
        <div className={cn('text-[11px]', TEXT.subtitle)}>点击定位 · hover ✕ 移除</div>
      </TooltipContent>
    </Tooltip>
  );
}

function MinimapGridStatic({
  devices,
  embedded,
  readinessByDeviceId,
  hostMap,
  highlightId,
  onLocate,
  onRemove,
}: Pick<
  SelectedMinimapProps,
  'devices' | 'embedded' | 'readinessByDeviceId' | 'hostMap' | 'highlightId' | 'onLocate' | 'onRemove'
>) {
  const tileMin = embedded ? 22 : 28;

  const renderTile = (device: ReadinessDevice) => {
    const row = readinessByDeviceId.get(device.id);
    const blocked = Boolean(row && !row.ready);
    const host = hostMap.get(String(device.host_id ?? 'unassigned'));
    const hostLabel = host?.ip || host?.name || String(device.host_id ?? '节点未知');
    return (
      <MinimapTile
        key={device.id}
        device={device}
        blocked={blocked}
        hostLabel={hostLabel}
        flash={highlightId === device.id}
        row={row}
        onLocate={onLocate}
        onRemove={onRemove}
      />
    );
  };

  return (
    <div
      className="grid gap-1"
      style={{ gridTemplateColumns: `repeat(auto-fill, minmax(${tileMin}px, 1fr))` }}
      data-testid="selected-minimap-grid"
    >
      {devices.map(renderTile)}
    </div>
  );
}

function MinimapGridVirtual({
  devices,
  embedded,
  readinessByDeviceId,
  hostMap,
  highlightId,
  onLocate,
  onRemove,
}: Pick<
  SelectedMinimapProps,
  'devices' | 'embedded' | 'readinessByDeviceId' | 'hostMap' | 'highlightId' | 'onLocate' | 'onRemove'
>) {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const tileMin = embedded ? 22 : 28;
  const [cols, setCols] = useState(6);

  useLayoutEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const measure = () => {
      const width = el.clientWidth - 4;
      const next = Math.max(1, Math.floor(width / (tileMin + 4)));
      setCols((prev) => (prev === next ? prev : next));
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [tileMin]);

  const renderTile = (device: ReadinessDevice) => {
    const row = readinessByDeviceId.get(device.id);
    const blocked = Boolean(row && !row.ready);
    const host = hostMap.get(String(device.host_id ?? 'unassigned'));
    const hostLabel = host?.ip || host?.name || String(device.host_id ?? '节点未知');
    return (
      <MinimapTile
        key={device.id}
        device={device}
        blocked={blocked}
        hostLabel={hostLabel}
        flash={highlightId === device.id}
        row={row}
        onLocate={onLocate}
        onRemove={onRemove}
      />
    );
  };

  const rowCount = Math.ceil(devices.length / cols);
  const rowVirtualizer = useVirtualizer({
    count: rowCount,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => tileMin + 4,
    overscan: 4,
  });

  return (
    <div
      ref={scrollRef}
      className="max-h-52 min-h-20 overflow-y-auto"
      data-testid="selected-minimap-grid"
      data-virtual="true"
    >
      <div className="relative w-full" style={{ height: `${rowVirtualizer.getTotalSize()}px` }}>
        {rowVirtualizer.getVirtualItems().map((virtualRow) => {
          const start = virtualRow.index * cols;
          const rowDevices = devices.slice(start, start + cols);
          return (
            <div
              key={virtualRow.key}
              className="absolute left-0 top-0 grid w-full gap-1"
              style={{
                height: `${virtualRow.size}px`,
                transform: `translateY(${virtualRow.start}px)`,
                gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))`,
              }}
            >
              {rowDevices.map(renderTile)}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function MinimapGrid({
  devices,
  embedded,
  readinessByDeviceId,
  hostMap,
  highlightId,
  onLocate,
  onRemove,
}: Pick<
  SelectedMinimapProps,
  'devices' | 'embedded' | 'readinessByDeviceId' | 'hostMap' | 'highlightId' | 'onLocate' | 'onRemove'
>) {
  if (devices.length === 0) {
    return (
      <div className={cn('flex min-h-20 items-center justify-center text-xs', TEXT.subtitle)}>尚未选择样机</div>
    );
  }

  if (devices.length > MINIMAP_VIRTUAL_THRESHOLD) {
    return (
      <MinimapGridVirtual
        devices={devices}
        embedded={embedded}
        readinessByDeviceId={readinessByDeviceId}
        hostMap={hostMap}
        highlightId={highlightId}
        onLocate={onLocate}
        onRemove={onRemove}
      />
    );
  }

  return (
    <MinimapGridStatic
      devices={devices}
      embedded={embedded}
      readinessByDeviceId={readinessByDeviceId}
      hostMap={hostMap}
      highlightId={highlightId}
      onLocate={onLocate}
      onRemove={onRemove}
    />
  );
}

export function SelectedMinimap({
  devices,
  readinessByDeviceId,
  hostMap,
  highlightId,
  embedded = false,
  onLocate,
  onRemove,
  onCopySerials,
  onDownloadCsv,
}: SelectedMinimapProps) {
  const tools = (
    <div className={cn('flex flex-wrap items-center gap-2', embedded && 'w-full')}>
      {!embedded && <MinimapLegend />}
      <Button
        type="button"
        variant="outline"
        size="sm"
        className={cn('h-7 gap-1 px-2 text-xs', embedded && 'flex-1')}
        disabled={devices.length === 0}
        onClick={onCopySerials}
      >
        <Copy className="h-3 w-3" />
        复制 serials
      </Button>
      <Button
        type="button"
        variant="outline"
        size="sm"
        className={cn('h-7 gap-1 px-2 text-xs', embedded && 'flex-1')}
        disabled={devices.length === 0}
        onClick={onDownloadCsv}
      >
        <Download className="h-3 w-3" />
        下载 CSV
      </Button>
    </div>
  );

  const grid = (
    <TooltipProvider delayDuration={200}>
      <MinimapGrid
        devices={devices}
        embedded={embedded}
        readinessByDeviceId={readinessByDeviceId}
        hostMap={hostMap}
        highlightId={highlightId}
        onLocate={onLocate}
        onRemove={onRemove}
      />
    </TooltipProvider>
  );

  if (embedded) {
    return (
      <div data-testid="selected-minimap" data-embedded="true">
        <span className="sr-only">已选样机 Minimap</span>
        <div className="mb-2">
          <MinimapLegend compact />
        </div>
        {grid}
        <div className="mt-3">{tools}</div>
      </div>
    );
  }

  return (
    <div className="rounded-lg border p-3" data-testid="selected-minimap">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <div>
          <div className="text-sm font-medium">已选样机 Minimap</div>
          <div className={cn('text-xs', TEXT.subtitle)}>
            跨节点汇总本次已选的 {devices.length} 台样机 · 点击定位 · hover ✕ 移除
          </div>
        </div>
        {tools}
      </div>
      {grid}
    </div>
  );
}
