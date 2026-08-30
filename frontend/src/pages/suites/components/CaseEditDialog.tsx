import { useState } from 'react';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { FORM } from '@/design-system';
import type { TestCase, TestCaseInput } from '@/utils/api/types';

type Props = {
  isOpen: boolean;
  isSubmitting?: boolean;
  initial?: TestCase | null;
  onClose: () => void;
  onSubmit: (payload: TestCaseInput) => void;
};

const EMPTY_EXEC_DESCS = '[]';

export default function CaseEditDialog({
  isOpen,
  isSubmitting = false,
  initial = null,
  onClose,
  onSubmit,
}: Props) {
  const [name, setName] = useState('');
  const [times, setTimes] = useState('1');
  const [enabled, setEnabled] = useState(true);
  const [execDescsJson, setExecDescsJson] = useState(EMPTY_EXEC_DESCS);
  const [error, setError] = useState('');

  const [prevKey, setPrevKey] = useState<string | null>(null);
  const dialogKey = initial ? `edit-${initial.id}` : 'create';
  if (prevKey !== dialogKey) {
    setPrevKey(dialogKey);
    if (isOpen) {
      setName(initial?.name ?? '');
      setTimes(String(initial?.times ?? 1));
      setEnabled(initial?.enabled ?? true);
      setExecDescsJson(
        initial ? JSON.stringify(initial.exec_descs ?? [], null, 2) : EMPTY_EXEC_DESCS,
      );
      setError('');
    }
  }

  const handleSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    const trimmed = name.trim();
    if (!trimmed) {
      setError('请填写用例名称');
      return;
    }
    const parsedTimes = Number(times);
    if (!Number.isFinite(parsedTimes) || parsedTimes < 1) {
      setError('执行次数须 ≥ 1');
      return;
    }
    let execDescs: Record<string, unknown>[];
    try {
      const parsed = JSON.parse(execDescsJson);
      if (!Array.isArray(parsed)) {
        setError('exec_descs 须为 JSON 数组');
        return;
      }
      execDescs = parsed;
    } catch {
      setError('exec_descs JSON 格式无效');
      return;
    }
    onSubmit({
      name: trimmed,
      times: parsedTimes,
      enabled,
      ordinal: initial?.ordinal ?? 0,
      exec_descs: execDescs,
    });
  };

  return (
    <Dialog open={isOpen} onOpenChange={(open) => !open && !isSubmitting && onClose()}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>{initial ? '编辑用例' : '新增用例'}</DialogTitle>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="space-y-3">
          <div>
            <label className={FORM.label} htmlFor="case-name">用例名称</label>
            <Input
              id="case-name"
              data-testid="case-name-input"
              value={name}
              onChange={(e) => { setName(e.target.value); setError(''); }}
              disabled={isSubmitting}
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className={FORM.label} htmlFor="case-times">执行次数</label>
              <Input
                id="case-times"
                type="number"
                min={1}
                value={times}
                onChange={(e) => setTimes(e.target.value)}
                disabled={isSubmitting}
              />
            </div>
            <div className="flex items-end pb-2">
              <label className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={enabled}
                  onChange={(e) => setEnabled(e.target.checked)}
                  disabled={isSubmitting}
                />
                启用
              </label>
            </div>
          </div>
          <div>
            <label className={FORM.label} htmlFor="case-exec-descs">exec_descs（JSON 数组，整覆盖）</label>
            <textarea
              id="case-exec-descs"
              data-testid="case-exec-descs"
              className="min-h-[160px] w-full rounded-md border border-input bg-background px-3 py-2 font-mono text-xs"
              value={execDescsJson}
              onChange={(e) => { setExecDescsJson(e.target.value); setError(''); }}
              disabled={isSubmitting}
            />
          </div>
          {error && <p className="text-sm text-destructive">{error}</p>}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose} disabled={isSubmitting}>
              取消
            </Button>
            <Button type="submit" data-testid="case-submit" disabled={isSubmitting}>
              保存
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
