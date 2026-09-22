import { CheckCheck, Download, RotateCw, Trash2, Wrench, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { BULK_BAR_INNER_CLASS, BULK_BAR_OUTER_CLASS } from '@/components/ui/bulk-action-bar';

export interface BulkActionCounts {
  selected: number;
  /** 从未安装且非 ONLINE → 首次安装 */
  firstInstall: number;
  /** 已安装且非 ONLINE → 重新安装 */
  reinstall: number;
  /** ONLINE → 可进入安全热更新预检 */
  hotUpdate: number;
  /** 已安装主机 → 可补齐刷机前置 */
  flashPrereqs: number;
}

interface Props {
  counts: BulkActionCounts;
  isAdmin: boolean;
  installPending?: boolean;
  hotUpdatePending?: boolean;
  flashPrereqsPending?: boolean;
  hotUpdateProgressLabel?: string;
  onInstall: () => void;
  onHotUpdate?: () => void;
  onFlashPrereqs?: () => void;
  onDelete?: () => void;
  onClear: () => void;
}

export default function HostBulkActionBar({
  counts,
  isAdmin,
  installPending,
  hotUpdatePending = false,
  flashPrereqsPending = false,
  hotUpdateProgressLabel,
  onInstall,
  onHotUpdate,
  onFlashPrereqs,
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
  const busy = Boolean(installPending || hotUpdatePending || flashPrereqsPending);
  const canHotUpdate = counts.hotUpdate > 0 && !!onHotUpdate && !busy;
  const canFlashPrereqs = counts.flashPrereqs > 0 && !!onFlashPrereqs && !busy;
  const hotUpdateDisabledReason = counts.hotUpdate === 0
    ? '选中的主机当前不在线，无法热更新'
    : busy
      ? '有主机运维操作正在执行'
      : '当前主机无法热更新';
  const breakdown = [
    counts.firstInstall > 0 ? `首次安装 ${counts.firstInstall}` : null,
    counts.reinstall > 0 ? `重新安装 ${counts.reinstall}` : null,
    counts.hotUpdate > 0 ? `在线 ${counts.hotUpdate}` : null,
    counts.flashPrereqs > 0 ? `可补刷机前置 ${counts.flashPrereqs}` : null,
  ].filter(Boolean).join(' · ');

  return (
    <div
      data-testid="host-bulk-action-bar"
      aria-live="polite"
      className={BULK_BAR_OUTER_CLASS}
    >
      <div className={BULK_BAR_INNER_CLASS}>
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

        <div className="hidden h-9 w-px bg-border md:block" />

        {isAdmin && (
          <div className="flex flex-1 flex-wrap items-center gap-2 sm:flex-none">
            <Button
              size="sm"
              variant="default"
              data-testid="host-bulk-install"
              disabled={installable === 0 || busy}
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
              title={canHotUpdate ? '预检并安全热更新选中的在线主机' : hotUpdateDisabledReason}
              onClick={onHotUpdate}
              className="gap-1"
            >
              <RotateCw className="h-3.5 w-3.5" />
              {hotUpdatePending
                ? (hotUpdateProgressLabel || '热更新中…')
                : `热更新${counts.hotUpdate > 1 ? ` (${counts.hotUpdate})` : ''}`}
            </Button>

            {onFlashPrereqs && (
              <Button
                size="sm"
                variant="outline"
                data-testid="host-bulk-flash-prereqs"
                disabled={!canFlashPrereqs}
                title={
                  canFlashPrereqs
                    ? '补齐 dialout / udev / Qt 刷机依赖（不跑热更新）'
                    : counts.flashPrereqs === 0
                      ? '选中主机均未安装 Agent，无法补齐刷机前置'
                      : '有主机运维操作正在执行'
                }
                onClick={onFlashPrereqs}
                className="gap-1"
              >
                <Wrench className="h-3.5 w-3.5" />
                {flashPrereqsPending
                  ? '补齐中…'
                  : `刷机前置${counts.flashPrereqs > 1 ? ` (${counts.flashPrereqs})` : ''}`}
              </Button>
            )}

            {onDelete && (
              <Button
                size="sm"
                variant="destructive"
                data-testid="host-bulk-delete"
                onClick={onDelete}
                disabled={busy}
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
          onClick={onClear}
          disabled={busy}
          className="gap-1 text-muted-foreground"
        >
          <X className="h-3.5 w-3.5" />
          取消选择
        </Button>
      </div>
    </div>
  );
}
