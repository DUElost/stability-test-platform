import { TEXT } from '@/design-system/tokens';
import { cn } from '@/lib/utils';
import type { ReadinessDevice } from '@/utils/planExecuteReadiness';
import type { PlanExecutePreset } from './planExecutePresets';
import { SelectedMinimap } from './SelectedMinimap';
import { SelectionPresets } from './SelectionPresets';

interface SelectedDevicesRailProps {
  devices: ReadinessDevice[];
  readinessByDeviceId: Map<number, { ready: boolean; reasons: string[] }>;
  hostMap: Map<string, { ip?: string | null; name?: string | null }>;
  highlightId?: number | null;
  presets: PlanExecutePreset[];
  onLocate: (deviceId: number) => void;
  onRemove: (deviceId: number) => void;
  onCopySerials: () => void;
  onDownloadCsv: () => void;
  onSavePreset: (name: string) => void;
  onApplyPreset: (preset: PlanExecutePreset) => void;
  onDeletePreset: (presetId: string) => void;
}

/** 选机工作台右栏：已选 Minimap + 导出 + 个人方案（对齐 mockup 三栏壳）。 */
export function SelectedDevicesRail({
  devices,
  readinessByDeviceId,
  hostMap,
  highlightId,
  presets,
  onLocate,
  onRemove,
  onCopySerials,
  onDownloadCsv,
  onSavePreset,
  onApplyPreset,
  onDeletePreset,
}: SelectedDevicesRailProps) {
  return (
    <aside
      className="flex h-full min-h-0 flex-col overflow-hidden rounded-xl border bg-card shadow-sm"
      data-testid="selected-devices-rail"
    >
      <div className="flex items-center justify-between gap-2 border-b px-3 py-2.5">
        <span className="text-sm font-semibold">已选集</span>
        <span className="rounded-full bg-muted px-2 py-0.5 text-xs font-medium tabular-nums">
          {devices.length}
        </span>
      </div>
      <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-auto p-3">
        <p className={cn('text-[11px] leading-4', TEXT.subtitle)}>
          点方块 = 定位高亮 · hover 右上 ✕ 才移除
        </p>
        <SelectedMinimap
          embedded
          devices={devices}
          readinessByDeviceId={readinessByDeviceId}
          hostMap={hostMap}
          highlightId={highlightId}
          onLocate={onLocate}
          onRemove={onRemove}
          onCopySerials={onCopySerials}
          onDownloadCsv={onDownloadCsv}
        />
        <SelectionPresets
          embedded
          presets={presets}
          selectedCount={devices.length}
          onSave={onSavePreset}
          onApply={onApplyPreset}
          onDelete={onDeletePreset}
        />
      </div>
    </aside>
  );
}
