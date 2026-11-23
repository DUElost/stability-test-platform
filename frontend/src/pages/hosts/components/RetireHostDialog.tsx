import { useState } from 'react';
import { Archive, ArchiveRestore } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { FORM } from '@/design-system';

export type HostRetireMode = 'retire' | 'unretire';

interface RetireHostDialogProps {
  isOpen: boolean;
  mode: HostRetireMode;
  hostLabel: string;
  isSubmitting?: boolean;
  onClose: () => void;
  onSubmit: (mode: HostRetireMode, reason: string) => void;
}

/**
 * ADR-0038 D2 主机退役 / 解除退役标准对话框（替换原 window.prompt，#4117 U3-1）。
 *
 * - 原因必填（写入审计 who/when/reason）；
 * - 使用标准 ui/dialog.tsx，支持 Esc 关闭与焦点归位。
 */
export function RetireHostDialog({
  isOpen,
  mode,
  hostLabel,
  isSubmitting = false,
  onClose,
  onSubmit,
}: RetireHostDialogProps) {
  const [reason, setReason] = useState('');
  const [error, setError] = useState('');

  const [prevKey, setPrevKey] = useState(`${isOpen}:${mode}:${hostLabel}`);
  const currentKey = `${isOpen}:${mode}:${hostLabel}`;
  if (prevKey !== currentKey) {
    setPrevKey(currentKey);
    if (isOpen) {
      setReason('');
      setError('');
    }
  }

  const isRetire = mode === 'retire';
  const title = isRetire ? '退役主机' : '解除主机退役';

  const handleSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    const trimmed = reason.trim();
    if (!trimmed) {
      setError(`${isRetire ? '退役' : '解除退役'}原因不能为空（审计必填）`);
      return;
    }
    onSubmit(mode, trimmed);
  };

  return (
    <Dialog open={isOpen} onOpenChange={(open) => !open && !isSubmitting && onClose()}>
      <DialogContent data-testid="host-retire-dialog">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            {isRetire ? (
              <Archive className="h-5 w-5 text-destructive-text" />
            ) : (
              <ArchiveRestore className="h-5 w-5 text-primary" />
            )}
            {title}
          </DialogTitle>
          <DialogDescription>
            {isRetire
              ? `将主机「${hostLabel}」标记为退役。退役后不再接受派发、安装与热更新，可随时在勾选「显示已退役」后解除退役。`
              : `将主机「${hostLabel}」解除退役，恢复调度与控制面运维操作。`}
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label htmlFor="host-retire-reason" className={FORM.label}>
              {isRetire ? '退役原因' : '解除退役原因'}
            </label>
            <input
              id="host-retire-reason"
              data-testid="host-retire-reason-input"
              value={reason}
              onChange={(event) => {
                setReason(event.target.value);
                setError('');
              }}
              placeholder={isRetire ? '例如：主机下架 / 硬件报废' : '例如：维修完成重新上线'}
              className={FORM.input}
              disabled={isSubmitting}
              autoFocus
            />
            {error ? (
              <p className={FORM.error} role="alert">
                {error}
              </p>
            ) : (
              <p className={FORM.hint}>原因必填，将写入审计日志（who / when / reason）。</p>
            )}
          </div>

          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose} disabled={isSubmitting}>
              取消
            </Button>
            <Button
              type="submit"
              variant={isRetire ? 'destructive' : 'default'}
              disabled={isSubmitting}
              data-testid="host-retire-submit"
            >
              {isSubmitting ? '提交中…' : isRetire ? '确认退役' : '确认解除'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
