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

interface SelectedMinimapProps {
  devices: ReadinessDevice[];
  readinessByDeviceId: Map<number, { ready: boolean; reasons: string[] }>;
  hostMap: Map<string, { ip?: string | null; name?: string | null }>;
  highlightId?: number | null;
  onLocate: (deviceId: number) => void;
  onRemove: (deviceId: number) => void;
  onCopySerials: () => void;
  onDownloadCsv: () => void;
}

export function SelectedMinimap({
  devices,
  readinessByDeviceId,
  hostMap,
  highlightId,
  onLocate,
  onRemove,
  onCopySerials,
  onDownloadCsv,
}: SelectedMinimapProps) {
  return (
    <div className="rounded-lg border p-3">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <div>
          <div className="text-sm font-medium">已选样机 Minimap</div>
          <div className={cn('text-xs', TEXT.subtitle)}>
            跨节点汇总本次已选的 {devices.length} 台样机 · 点击定位 · hover ✕ 移除
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex gap-3 text-xs">
            <span><i className="mr-1 inline-block h-2.5 w-2.5 rounded-sm bg-success" />已选就绪</span>
            <span className="inline-flex items-center gap-1">
              <i
                className="inline-block h-2.5 w-2.5 rounded-sm border border-destructive bg-destructive"
                style={{
                  backgroundImage:
                    'repeating-linear-gradient(45deg, transparent, transparent 1px, rgba(255,255,255,0.55) 1px, rgba(255,255,255,0.55) 2px)',
                }}
              />
              已选阻塞（斜纹）
            </span>
          </div>
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="h-7 gap-1 px-2 text-xs"
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
            className="h-7 gap-1 px-2 text-xs"
            disabled={devices.length === 0}
            onClick={onDownloadCsv}
          >
            <Download className="h-3 w-3" />
            下载 CSV
          </Button>
        </div>
      </div>
      {devices.length === 0 ? (
        <div className={cn('flex min-h-20 items-center justify-center text-xs', TEXT.subtitle)}>尚未选择样机</div>
      ) : (
        <TooltipProvider delayDuration={200}>
          <div className="grid gap-1" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(28px, 1fr))' }}>
            {devices.map((device) => {
              const row = readinessByDeviceId.get(device.id);
              const blocked = Boolean(row && !row.ready);
              const host = hostMap.get(String(device.host_id ?? 'unassigned'));
              const hostLabel = host?.ip || host?.name || device.host_id || '节点未知';
              const flash = highlightId === device.id;
              return (
                <Tooltip key={device.id}>
                  <TooltipTrigger asChild>
                    <button
                      type="button"
                      data-minimap-device-id={device.id}
                      aria-label={`定位已选设备 ${device.id}${blocked ? ' 阻塞' : ''}`}
                      onClick={() => onLocate(device.id)}
                      className={cn(
                        'group relative flex aspect-square items-center justify-center overflow-visible rounded-sm border transition-transform hover:z-10 hover:scale-110 hover:ring-2 hover:ring-primary/40',
                        blocked ? 'border-destructive bg-destructive' : 'border-success bg-success',
                        flash && 'animate-pulse ring-2 ring-primary',
                      )}
                    >
                      {blocked && (
                        <span
                          aria-hidden
                          className="pointer-events-none absolute inset-0 overflow-hidden rounded-sm opacity-70"
                          style={{
                            backgroundImage:
                              'repeating-linear-gradient(45deg, transparent, transparent 2px, rgba(255,255,255,0.45) 2px, rgba(255,255,255,0.45) 4px)',
                          }}
                        />
                      )}
                      <span
                        role="button"
                        tabIndex={0}
                        aria-label={`移除已选设备 ${device.id}`}
                        className="absolute -right-1 -top-1 z-20 flex h-3.5 w-3.5 items-center justify-center rounded-full bg-destructive text-primary-foreground opacity-0 shadow transition-opacity group-hover:opacity-100 focus-visible:opacity-100"
                        onClick={(event) => {
                          event.preventDefault();
                          event.stopPropagation();
                          onRemove(device.id);
                        }}
                        onKeyDown={(event) => {
                          if (event.key === 'Enter' || event.key === ' ') {
                            event.preventDefault();
                            event.stopPropagation();
                            onRemove(device.id);
                          }
                        }}
                      >
                        <X className="h-2.5 w-2.5" strokeWidth={3} aria-hidden />
                      </span>
                    </button>
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
            })}
          </div>
        </TooltipProvider>
      )}
    </div>
  );
}
