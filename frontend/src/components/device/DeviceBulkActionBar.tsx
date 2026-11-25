import {
  Archive,
  ArchiveRestore,
  CheckCheck,
  ChevronDown,
  Copy,
  Download,
  FolderKanban,
  MoreHorizontal,
  Pointer,
  SkipForward,
  Tags,
  X,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { BULK_BAR_INNER_CLASS, BULK_BAR_OUTER_CLASS } from '@/components/ui/bulk-action-bar';

interface DeviceBulkActionBarProps {
  selectedCount: number;
  filteredCount: number;
  selectedFilteredCount: number;
  statusSummary?: string;
  /** #496 B3-1：显式区分当前页勾选与跨页筛选全选。 */
  selectionScope?: 'page' | 'all_filtered';
  crossPagePreviewLoading?: boolean;
  /** 跨页预览超限时仅允许只读动作（导出/复制），禁用写动作。 */
  writeLimitExceeded?: boolean;
  writeMaxAllowed?: number | null;
  canEditTags?: boolean;
  tagUpdatePending?: boolean;
  /** ADR-0029：批量归入项目（admin 动作，非 admin 不显示入口） */
  canAssignProject?: boolean;
  /** admin：批量开关滑动留痕（show_touches + pointer_location） */
  canSwipeTrail?: boolean;
  swipeTrailPending?: boolean;
  /** admin：批量快捷跳过 OOBE（复用脚本库 oobe_skip） */
  canSkipOobe?: boolean;
  skipOobePending?: boolean;
  /** ADR-0057 D2（#2962 B）：批量退役 / 解除退役（admin 动作） */
  canRetire?: boolean;
  retirePending?: boolean;
  exportPending?: boolean;
  onSelectAllFiltered: () => void;
  onEditTags: () => void;
  onAssignProject?: () => void;
  onSwipeTrailOn?: () => void;
  onSwipeTrailOff?: () => void;
  onSkipOobe?: () => void;
  onRetire?: () => void;
  onUnretire?: () => void;
  onCopySerials: () => void;
  onExport: () => void;
  onClear: () => void;
}

/**
 * 设备批量操作条（#4117 U2-1a 收敛）：
 * 主区保留 3 个高频动作（批量标签 / 归入项目 / 导出 CSV），
 * 复制序列号、滑动留痕、快捷跳过OOBE 与退役/解除退役收拢至「更多操作」DropdownMenu，
 * 避免平铺按钮在 1280×800 折行撑高底栏。
 */
export default function DeviceBulkActionBar({
  selectedCount,
  filteredCount,
  selectedFilteredCount,
  statusSummary,
  selectionScope = 'page',
  crossPagePreviewLoading = false,
  writeLimitExceeded = false,
  writeMaxAllowed = null,
  canEditTags = false,
  tagUpdatePending = false,
  canAssignProject = false,
  canSwipeTrail = false,
  swipeTrailPending = false,
  canSkipOobe = false,
  skipOobePending = false,
  canRetire = false,
  retirePending = false,
  exportPending = false,
  onSelectAllFiltered,
  onEditTags,
  onAssignProject = () => {},
  onSwipeTrailOn = () => {},
  onSwipeTrailOff = () => {},
  onSkipOobe = () => {},
  onRetire = () => {},
  onUnretire = () => {},
  onCopySerials,
  onExport,
  onClear,
}: DeviceBulkActionBarProps) {
  if (selectedCount === 0) return null;

  const canSelectAllFiltered =
    selectionScope !== 'all_filtered' && filteredCount > 0 && selectedFilteredCount < filteredCount;
  const writeLimitHint = writeLimitExceeded
    ? `跨页匹配超过单次写上限（${writeMaxAllowed ?? 500} 台），请收窄筛选后再执行写操作`
    : undefined;

  return (
    <div
      data-testid="device-bulk-action-bar"
      aria-live="polite"
      className={BULK_BAR_OUTER_CLASS}
    >
      <div className={BULK_BAR_INNER_CLASS}>
        <div className="flex min-w-0 items-center gap-3">
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary">
            <CheckCheck className="h-4 w-4" />
          </div>
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-1.5 text-sm font-medium text-foreground">
              {/* #2600：只报「已选择 N 台」时，页选（50）与全量（546）读数同形——
                  分母写出来，规模差才可见；右侧「选择全部筛选结果 (M)」是补齐入口。 */}
              <span>
                已选择 <span className="font-mono text-primary">{selectedCount}</span>
                {filteredCount > 0 ? (
                  <>
                    <span className="text-muted-foreground"> / </span>
                    <span className="font-mono text-muted-foreground">{filteredCount}</span>
                  </>
                ) : null}{' '}
                台设备
              </span>
              {selectionScope === 'all_filtered' ? (
                <span
                  data-testid="device-selection-scope-badge"
                  className="rounded-full bg-primary/10 px-1.5 py-0.5 text-[10px] font-medium text-primary"
                >
                  跨页已冻结
                </span>
              ) : null}
            </div>
            <div className="truncate text-[11px] text-muted-foreground">
              {writeLimitExceeded
                ? `已超单次写操作上限（${writeMaxAllowed ?? 500} 台）：仅支持复制与导出 CSV，请收窄筛选`
                : statusSummary || `当前筛选共 ${filteredCount} 台`}
            </div>
          </div>
        </div>

        {canSelectAllFiltered && (
          <Button
            size="sm"
            variant="ghost"
            data-testid="device-select-all-filtered"
            disabled={crossPagePreviewLoading}
            onClick={onSelectAllFiltered}
            className="h-7 text-[11px] text-primary"
          >
            {crossPagePreviewLoading ? '预览中…' : `选择全部筛选结果 (${filteredCount})`}
          </Button>
        )}

        <div className="hidden h-9 w-px bg-border md:block" />

        <div className="flex flex-1 flex-wrap items-center gap-2 md:flex-none">
          <Button
            size="sm"
            variant="default"
            data-testid="device-bulk-tags"
            disabled={!canEditTags || tagUpdatePending || writeLimitExceeded}
            title={!canEditTags ? '需要管理员权限' : writeLimitHint}
            onClick={onEditTags}
            className="gap-1"
          >
            <Tags className="h-3.5 w-3.5" />
            {tagUpdatePending ? '更新中…' : '批量标签'}
          </Button>

          <Button
            size="sm"
            variant="default"
            data-testid="device-bulk-assign-project"
            disabled={!canAssignProject || writeLimitExceeded}
            title={!canAssignProject ? '需要管理员权限' : writeLimitHint}
            onClick={onAssignProject}
            className="gap-1"
          >
            <FolderKanban className="h-3.5 w-3.5" />
            归入项目
          </Button>

          <Button size="sm" variant="outline" disabled={exportPending} onClick={onExport} className="gap-1">
            <Download className="h-3.5 w-3.5" />
            {exportPending ? '导出中…' : '导出 CSV'}
          </Button>

          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                size="sm"
                variant="outline"
                data-testid="device-bulk-more"
                className="gap-1"
              >
                <MoreHorizontal className="h-3.5 w-3.5" />
                更多操作
                <ChevronDown className="h-3 w-3 opacity-70" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" side="top" className="w-44">
              <DropdownMenuItem
                data-testid="device-bulk-copy-serials"
                disabled={exportPending}
                onSelect={onCopySerials}
                className="gap-2 text-xs"
              >
                <Copy className="h-3.5 w-3.5 text-muted-foreground" />
                复制序列号
              </DropdownMenuItem>

              {canSwipeTrail && (
                <>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem
                    data-testid="device-bulk-swipe-trail-on"
                    disabled={swipeTrailPending || writeLimitExceeded}
                    onSelect={onSwipeTrailOn}
                    className="gap-2 text-xs"
                  >
                    <Pointer className="h-3.5 w-3.5 text-muted-foreground" />
                    {swipeTrailPending ? '下发中…' : '开启滑动留痕'}
                  </DropdownMenuItem>
                  <DropdownMenuItem
                    data-testid="device-bulk-swipe-trail-off"
                    disabled={swipeTrailPending || writeLimitExceeded}
                    onSelect={onSwipeTrailOff}
                    className="gap-2 text-xs"
                  >
                    <Pointer className="h-3.5 w-3.5 text-muted-foreground" />
                    {swipeTrailPending ? '下发中…' : '关闭滑动留痕'}
                  </DropdownMenuItem>
                </>
              )}

              {canSkipOobe && (
                <>
                  {!canSwipeTrail && <DropdownMenuSeparator />}
                  <DropdownMenuItem
                    data-testid="device-bulk-skip-oobe"
                    disabled={skipOobePending || writeLimitExceeded}
                    onSelect={onSkipOobe}
                    className="gap-2 text-xs"
                  >
                    <SkipForward className="h-3.5 w-3.5 text-muted-foreground" />
                    {skipOobePending ? '跳过中…' : '快捷跳过OOBE'}
                  </DropdownMenuItem>
                </>
              )}

              {canRetire && (
                <>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem
                    data-testid="device-bulk-retire"
                    disabled={retirePending || writeLimitExceeded}
                    onSelect={onRetire}
                    className="gap-2 text-xs text-destructive focus:text-destructive"
                  >
                    <Archive className="h-3.5 w-3.5" />
                    {retirePending ? '退役中…' : '退役'}
                  </DropdownMenuItem>
                  <DropdownMenuItem
                    data-testid="device-bulk-unretire"
                    disabled={retirePending || writeLimitExceeded}
                    onSelect={onUnretire}
                    className="gap-2 text-xs"
                  >
                    <ArchiveRestore className="h-3.5 w-3.5 text-muted-foreground" />
                    {retirePending ? '处理中…' : '解除退役'}
                  </DropdownMenuItem>
                </>
              )}
            </DropdownMenuContent>
          </DropdownMenu>
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
