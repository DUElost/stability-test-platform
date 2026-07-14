import { CheckCheck, Download, RotateCw, Trash2, X } from 'lucide-react';
import { Button } from '@/components/ui/button';

export interface BulkActionCounts {
  selected: number;
  /** 从未安装且非 ONLINE → 首次安装 */
  firstInstall: number;
  /** 已安装且非 ONLINE → 重新安装 */
  reinstall: number;
  /** ONLINE → 可热更新（P0 仅展示计数，批量热更新未开放） */
  hotUpdate: number;
}

interface Props {
  counts: BulkActionCounts;
  isAdmin: boolean;
  installPending?: boolean;
  onInstall: () => void;
  onHotUpdate?: () => void;
  onDelete?: () => void;
  onClear: () => void;
}

export default function HostBulkActionBar({
  counts,
  isAdmin,
  installPending,
  onInstall,
  onHotUpdate,
  onDelete,
  onClear,
}: Props) {
  if (counts.selected === 0) return null;

  const installable = counts.firstInstall + counts.reinstall;
  const installLabel =
    counts.firstInstall > 0 && counts.reinstall > 0
      ? `安装 Agent (${installable})`
      : counts.reinstall > 0 && counts.firstInstall === 0
        ? `重新安装 (${counts.reinstall})`
        : `首次安装 (${counts.firstInstall})`;
  const canHotUpdate = counts.selected === 1 && counts.hotUpdate === 1 && !!onHotUpdate;
  const hotUpdateDisabledReason = counts.hotUpdate === 0
    ? '选中的主机当前不在线，无法热更新'
    : counts.selected > 1
      ? '暂不支持批量热更新，请仅选择一台在线主机'
      : '当前主机无法热更新';
  const breakdown = [
    counts.firstInstall > 0 ? `首次安装 ${counts.firstInstall}` : null,
    counts.reinstall > 0 ? `重新安装 ${counts.reinstall}` : null,
    counts.hotUpdate > 0 ? `在线 ${counts.hotUpdate}` : null,
  ].filter(Boolean).join(' · ');

  return (
    <div
      data-testid="host-bulk-action-bar"
      aria-live="polite"
      className="pointer-events-none fixed bottom-4 left-4 right-4 z-40 flex justify-center lg:left-60"
    >
      <div className="pointer-events-auto flex w-full max-w-4xl flex-wrap items-center gap-3 rounded-2xl border border-border bg-card/95 px-3 py-3 shadow-xl backdrop-blur supports-[backdrop-filter]:bg-card/90 sm:px-4">
        <div className="flex min-w-0 items-center gap-3">
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary">
            <CheckCheck className="h-4 w-4" />
          </div>
          <div className="min-w-0">
            <div className="text-sm font-medium text-foreground">
              已选择 <span className="font-mono text-primary">{counts.selected}</span> 台主机
            </div>
            <div className="truncate text-[11px] text-muted-foreground">
              {breakdown || '可执行删除或取消选择'}
            </div>
          </div>
        </div>

        <div className="hidden h-9 w-px bg-border sm:block" />

        {isAdmin && (
          <div className="flex flex-1 flex-wrap items-center gap-2 sm:flex-none">
            <Button
              size="sm"
              variant="default"
              data-testid="host-bulk-install"
              disabled={installable === 0 || installPending}
              title={
                installable === 0
                  ? '选中主机均已在线或无可安装目标（在线主机可单选后热更新）'
                  : undefined
              }
              onClick={onInstall}
              className="gap-1"
            >
              <Download className="h-3.5 w-3.5" />
              {installPending ? '安装中…' : installLabel}
            </Button>

            <Button
              size="sm"
              variant="outline"
              data-testid="host-bulk-hot-update"
              disabled={!canHotUpdate}
              title={canHotUpdate ? '热更新选中的在线主机' : hotUpdateDisabledReason}
              onClick={onHotUpdate}
              className="gap-1"
            >
              <RotateCw className="h-3.5 w-3.5" />
              热更新{counts.hotUpdate > 1 ? ` (${counts.hotUpdate})` : ''}
            </Button>

            {onDelete && (
              <Button
                size="sm"
                variant="destructive"
                data-testid="host-bulk-delete"
                onClick={onDelete}
                className="gap-1"
              >
                <Trash2 className="h-3.5 w-3.5" />
                删除{counts.selected > 1 ? ` (${counts.selected})` : ''}
              </Button>
            )}
          </div>
        )}

        <Button
          size="sm"
          variant="ghost"
          data-testid="host-bulk-clear"
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
