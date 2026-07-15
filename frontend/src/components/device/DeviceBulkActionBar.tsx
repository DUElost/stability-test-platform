import { Activity, CheckCheck, Copy, Download, Tags, X } from 'lucide-react';
import { Button } from '@/components/ui/button';

interface DeviceBulkActionBarProps {
  selectedCount: number;
  filteredCount: number;
  selectedFilteredCount: number;
  statusSummary?: string;
  canEditTags?: boolean;
  tagUpdatePending?: boolean;
  onSelectAllFiltered: () => void;
  onEditTags: () => void;
  onCopySerials: () => void;
  onExport: () => void;
  onViewMetrics: () => void;
  onClear: () => void;
}

export default function DeviceBulkActionBar({
  selectedCount,
  filteredCount,
  selectedFilteredCount,
  statusSummary,
  canEditTags = false,
  tagUpdatePending = false,
  onSelectAllFiltered,
  onEditTags,
  onCopySerials,
  onExport,
  onViewMetrics,
  onClear,
}: DeviceBulkActionBarProps) {
  if (selectedCount === 0) return null;

  const canSelectAllFiltered = filteredCount > 0 && selectedFilteredCount < filteredCount;

  return (
    <div
      data-testid="device-bulk-action-bar"
      aria-live="polite"
      className="pointer-events-none fixed bottom-4 left-4 right-4 z-40 flex justify-center lg:left-60"
    >
      <div className="pointer-events-auto flex w-full max-w-5xl flex-wrap items-center gap-3 rounded-2xl border border-border bg-card/95 px-3 py-3 shadow-xl backdrop-blur supports-[backdrop-filter]:bg-card/90 sm:px-4">
        <div className="flex min-w-0 items-center gap-3">
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary">
            <CheckCheck className="h-4 w-4" />
          </div>
          <div className="min-w-0">
            <div className="text-sm font-medium text-foreground">
              已选择 <span className="font-mono text-primary">{selectedCount}</span> 台设备
            </div>
            <div className="truncate text-[11px] text-muted-foreground">
              {statusSummary || `当前筛选共 ${filteredCount} 台`}
            </div>
          </div>
        </div>

        {canSelectAllFiltered && (
          <Button
            size="sm"
            variant="ghost"
            data-testid="device-select-all-filtered"
            onClick={onSelectAllFiltered}
            className="h-7 text-[11px] text-primary"
          >
            选择全部筛选结果 ({filteredCount})
          </Button>
        )}

        <div className="hidden h-9 w-px bg-border md:block" />

        <div className="flex flex-1 flex-wrap items-center gap-2 md:flex-none">
          {canEditTags && (
            <Button
              size="sm"
              variant="default"
              data-testid="device-bulk-tags"
              disabled={tagUpdatePending}
              onClick={onEditTags}
              className="gap-1"
            >
              <Tags className="h-3.5 w-3.5" />
              {tagUpdatePending ? '更新中…' : '批量标签'}
            </Button>
          )}

          <Button
            size="sm"
            variant="outline"
            data-testid="device-bulk-metrics"
            disabled={selectedCount !== 1}
            title={selectedCount === 1 ? '查看所选设备指标' : '仅选择一台设备时可查看指标'}
            onClick={onViewMetrics}
            className="gap-1"
          >
            <Activity className="h-3.5 w-3.5" />
            查看指标
          </Button>

          <Button size="sm" variant="outline" onClick={onCopySerials} className="gap-1">
            <Copy className="h-3.5 w-3.5" />
            复制序列号
          </Button>

          <Button size="sm" variant="outline" onClick={onExport} className="gap-1">
            <Download className="h-3.5 w-3.5" />
            导出 CSV
          </Button>
        </div>

        <Button
          size="sm"
          variant="ghost"
          data-testid="device-bulk-clear"
          aria-label="取消选择"
          title="取消选择"
          onClick={onClear}
          className="ml-auto h-8 w-8 shrink-0 p-0 text-muted-foreground"
        >
          <X className="h-4 w-4" />
        </Button>
      </div>
    </div>
  );
}
