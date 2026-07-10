import { Download, RotateCw, Trash2, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { TEXT } from '@/design-system/tokens';
import { cn } from '@/lib/utils';

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
  hotUpdateDisabledReason?: string;
}

export default function HostBulkActionBar({
  counts,
  isAdmin,
  installPending,
  onInstall,
  onHotUpdate,
  onDelete,
  onClear,
  hotUpdateDisabledReason = '批量热更新需先将热更新改为 SAQ 任务（P2）',
}: Props) {
  if (counts.selected === 0) return null;

  const installable = counts.firstInstall + counts.reinstall;
  const installLabel =
    counts.firstInstall > 0 && counts.reinstall > 0
      ? `安装 Agent (${installable}) · 首次 ${counts.firstInstall} / 重装 ${counts.reinstall}`
      : counts.reinstall > 0 && counts.firstInstall === 0
        ? `重新安装 (${counts.reinstall})`
        : `首次安装 (${counts.firstInstall})`;

  return (
    <div
      data-testid="host-bulk-action-bar"
      className="flex flex-wrap items-center gap-2 rounded-lg border border-border bg-card px-3 py-2"
    >
      <span className={cn('text-sm', TEXT.subtitle)}>
        已选 <b className="font-mono text-foreground">{counts.selected}</b> 台
      </span>

      {isAdmin && (
        <>
          <Button
            size="sm"
            variant="default"
            data-testid="host-bulk-install"
            disabled={installable === 0 || installPending}
            title={
              installable === 0
                ? '选中主机均已在线或无可安装目标（ONLINE 请用热更新）'
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
            disabled
            title={hotUpdateDisabledReason}
            onClick={onHotUpdate}
            className="gap-1"
          >
            <RotateCw className="h-3.5 w-3.5" />
            热更新 ({counts.hotUpdate})
          </Button>

          {onDelete && (
            <Button
              size="sm"
              variant="outline"
              data-testid="host-bulk-delete"
              onClick={onDelete}
              className="gap-1 text-destructive hover:text-destructive"
            >
              <Trash2 className="h-3.5 w-3.5" />
              删除
            </Button>
          )}
        </>
      )}

      <Button
        size="sm"
        variant="ghost"
        data-testid="host-bulk-clear"
        onClick={onClear}
        className="ml-auto gap-1"
      >
        <X className="h-3.5 w-3.5" />
        取消选择
      </Button>
    </div>
  );
}
