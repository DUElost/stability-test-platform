import { useState } from 'react';
import { ShieldCheck } from 'lucide-react';
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
import { cn } from '@/lib/utils';
import type {
  DeviceReturnOverrideReason,
  DeviceReturnOverrideRequest,
  DeviceReturnOverrideResult,
} from '@/utils/api/types';
import {
  OVERRIDE_REASON_OPTIONS,
  REGISTERED_FAMILIES,
  familyLabel,
} from './deviceReturn';

interface Props {
  isOpen: boolean;
  isSubmitting?: boolean;
  result: DeviceReturnOverrideResult | null;
  onClose: () => void;
  onSubmit: (payload: DeviceReturnOverrideRequest) => void;
}

const CHECKBOX = 'h-4 w-4 rounded border-border accent-primary';

/**
 * ADR-0061 D5.7 / #3723 U6：人工放行对话框（仅管理员入口）。
 *
 * 专项可多选或全部；原因类别只能从固定四值中选择；证据引用必填。
 * 提交成功后停留在结果态，展示关闭条目数、欠账租约是否释放与剩余条目数。
 */
export function DeviceReturnOverrideDialog({
  isOpen,
  isSubmitting = false,
  result,
  onClose,
  onSubmit,
}: Props) {
  const [allSelected, setAllSelected] = useState(false);
  const [selected, setSelected] = useState<string[]>([]);
  const [reason, setReason] = useState('');
  const [evidence, setEvidence] = useState('');
  const [familyError, setFamilyError] = useState('');
  const [reasonError, setReasonError] = useState('');
  const [evidenceError, setEvidenceError] = useState('');
  // React 官方「prop 变化时调整 state」模式：重新打开即清空上一次的输入/错误。
  const [wasOpen, setWasOpen] = useState(isOpen);
  if (isOpen !== wasOpen) {
    setWasOpen(isOpen);
    if (isOpen) {
      setAllSelected(false);
      setSelected([]);
      setReason('');
      setEvidence('');
      setFamilyError('');
      setReasonError('');
      setEvidenceError('');
    }
  }

  const toggleFamily = (family: string) => {
    setFamilyError('');
    setSelected((prev) =>
      prev.includes(family) ? prev.filter((item) => item !== family) : [...prev, family],
    );
  };

  const handleSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    const families = allSelected ? [] : selected;
    let invalid = false;
    if (!allSelected && families.length === 0) {
      setFamilyError('请选择至少一个专项，或勾选「全部专项」');
      invalid = true;
    }
    if (!reason) {
      setReasonError('请选择原因类别');
      invalid = true;
    }
    if (!evidence.trim()) {
      setEvidenceError('请填写证据引用（必填）');
      invalid = true;
    }
    if (invalid) return;
    onSubmit({
      families: allSelected ? null : families,
      reason_category: reason as DeviceReturnOverrideReason,
      evidence_ref: evidence.trim(),
    });
  };

  return (
    <Dialog open={isOpen} onOpenChange={(open) => !open && !isSubmitting && onClose()}>
      <DialogContent data-testid="return-override-dialog">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <ShieldCheck className="h-5 w-5 text-primary" />
            人工放行设备归还欠账
          </DialogTitle>
          <DialogDescription>
            关闭所选专项的全部未关闭条目，并记为「未验证（人工放行）」；不会产生「已归还」。
          </DialogDescription>
        </DialogHeader>

        {result ? (
          <div className="space-y-3" data-testid="return-override-result">
            <div className="rounded-lg border border-success/40 bg-success/10 px-3 py-2 text-sm text-success">
              已关闭 <span className="font-mono font-semibold">{result.closed_count}</span> 条欠账；
              {result.debt_lease_released ? '已释放欠账租约' : '未释放欠账租约'}；
              剩余 <span className="font-mono font-semibold">{result.open_debt_count}</span> 条。
            </div>
            <DialogFooter>
              <Button type="button" onClick={onClose} data-testid="return-override-close">
                关闭
              </Button>
            </DialogFooter>
          </div>
        ) : (
          <form onSubmit={handleSubmit} className="space-y-4">
            <fieldset>
              <legend className={FORM.label}>专项</legend>
              <label className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  className={CHECKBOX}
                  checked={allSelected}
                  disabled={isSubmitting}
                  onChange={(event) => {
                    setAllSelected(event.target.checked);
                    setFamilyError('');
                  }}
                  aria-label="全部专项"
                />
                全部专项
              </label>
              <div className="mt-2 grid grid-cols-2 gap-1.5">
                {REGISTERED_FAMILIES.map((family) => (
                  <label key={family} className="flex items-center gap-2 text-sm">
                    <input
                      type="checkbox"
                      className={CHECKBOX}
                      checked={!allSelected && selected.includes(family)}
                      disabled={isSubmitting || allSelected}
                      onChange={() => toggleFamily(family)}
                      aria-label={'专项 ' + familyLabel(family)}
                    />
                    {familyLabel(family)}
                  </label>
                ))}
              </div>
              {familyError ? <p className={FORM.error}>{familyError}</p> : null}
            </fieldset>

            <div>
              <label htmlFor="return-override-reason" className={FORM.label}>
                原因类别
              </label>
              <select
                id="return-override-reason"
                className={cn(FORM.select, 'w-full')}
                value={reason}
                disabled={isSubmitting}
                onChange={(event) => {
                  setReason(event.target.value);
                  setReasonError('');
                }}
              >
                <option value="">请选择原因类别</option>
                {OVERRIDE_REASON_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
              {reasonError ? <p className={FORM.error}>{reasonError}</p> : null}
            </div>

            <div>
              <label htmlFor="return-override-evidence" className={FORM.label}>
                证据引用
              </label>
              <input
                id="return-override-evidence"
                className={FORM.input}
                value={evidence}
                disabled={isSubmitting}
                placeholder="例如：刷机 job id / 现场确认记录编号"
                onChange={(event) => {
                  setEvidence(event.target.value);
                  setEvidenceError('');
                }}
              />
              {evidenceError ? (
                <p className={FORM.error}>{evidenceError}</p>
              ) : (
                <p className={FORM.hint}>写入审计，用于事后追溯放行依据。</p>
              )}
            </div>

            <DialogFooter>
              <Button type="button" variant="outline" onClick={onClose} disabled={isSubmitting}>
                取消
              </Button>
              <Button type="submit" disabled={isSubmitting} data-testid="return-override-submit">
                {isSubmitting ? '提交中…' : '确认放行'}
              </Button>
            </DialogFooter>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
}
