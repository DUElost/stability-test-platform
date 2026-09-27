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

export type DeviceRetireMode = 'retire' | 'unretire';

interface RetireDevicesDialogProps {
  isOpen: boolean;
  mode: DeviceRetireMode;
  selectedCount: number;
  /** 选中里已退役 / 未退役的台数——提交前让人看到「其中 N 台是幂等跳过」。 */
  alreadyRetiredCount?: number;
  isSubmitting?: boolean;
  onClose: () => void;
  onSubmit: (mode: DeviceRetireMode, reason: string) => void;
}

/**
 * ADR-0057 D2（#2962 B）：批量退役 / 解除退役。
 *
 * - 原因必填（审计 who/when/reason 的 reason，与主机侧 HostRetireIn 同款）；
 * - E2 前置（活跃 Job / ACTIVE 租约）由后端逐台校验：失败的那台记 conflict，
 *   不影响其余设备——结果逐台汇总在 toast 里。
 */
export function RetireDevicesDialog({
  isOpen,
  mode,
  selectedCount,
  alreadyRetiredCount = 0,
  isSubmitting = false,
  onClose,
  onSubmit,
}: RetireDevicesDialogProps) {
  const [reason, setReason] = useState('');
  const [error, setError] = useState('');

  const [prevKey, setPrevKey] = useState(`${isOpen}:${mode}`);
  const currentKey = `${isOpen}:${mode}`;
  if (prevKey !== currentKey) {
    setPrevKey(currentKey);
    if (isOpen) {
      setReason('');
      setError('');
    }
  }

  const isRetire = mode === 'retire';
  const title = isRetire ? '批量退役设备' : '批量解除退役';

  const handleSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    const trimmed = reason.trim();
    if (!trimmed) {
      setError('请填写原因（审计必填）');
      return;
    }
    onSubmit(mode, trimmed);
  };

  return (
    <Dialog open={isOpen} onOpenChange={(open) => !open && !isSubmitting && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            {isRetire
              ? <Archive className="h-5 w-5 text-destructive" />
              : <ArchiveRestore className="h-5 w-5 text-primary" />}
            {title}
          </DialogTitle>
          <DialogDescription>
            {isRetire
              ? `将对选中的 ${selectedCount} 台设备执行退役。需无活跃 Job、无 ACTIVE 租约，否则该台返回 409 并跳过（不顺带中止在跑测试）。`
              : `将对选中的 ${selectedCount} 台设备解除退役，全部派发/容量口径随之恢复。`}
            {alreadyRetiredCount > 0 && (
              <>
                {' '}其中 <span className="font-mono">{alreadyRetiredCount}</span> 台
                {isRetire ? '已是退役态（幂等跳过）' : '并未退役（幂等跳过）'}。
              </>
            )}
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label htmlFor="device-retire-reason" className={FORM.label}>
              {isRetire ? '退役原因' : '解除原因'}
            </label>
            <input
              id="device-retire-reason"
              value={reason}
              onChange={(event) => {
                setReason(event.target.value);
                setError('');
              }}
              placeholder={isRetire ? '例如：库存陈旧已报废 / 归还厂商' : '例如：设备修复回场'}
              className={FORM.input}
              disabled={isSubmitting}
              autoFocus
            />
            {error ? (
              <p className={FORM.error}>{error}</p>
            ) : (
              <p className={FORM.hint}>原因写入审计（who/when/reason），退役不改写设备 status。</p>
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
              data-testid="device-retire-submit"
            >
              {isSubmitting ? '提交中…' : isRetire ? '确认退役' : '确认解除'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
