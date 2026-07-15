import { useEffect, useState } from 'react';
import { Tags } from 'lucide-react';
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

export type DeviceTagOperation = 'add' | 'remove' | 'replace';

interface BatchEditDeviceTagsDialogProps {
  isOpen: boolean;
  selectedCount: number;
  isSubmitting?: boolean;
  onClose: () => void;
  onSubmit: (operation: DeviceTagOperation, tags: string[]) => void;
}

export function BatchEditDeviceTagsDialog({
  isOpen,
  selectedCount,
  isSubmitting = false,
  onClose,
  onSubmit,
}: BatchEditDeviceTagsDialogProps) {
  const [operation, setOperation] = useState<DeviceTagOperation>('add');
  const [tagInput, setTagInput] = useState('');
  const [error, setError] = useState('');

  useEffect(() => {
    if (!isOpen) return;
    setOperation('add');
    setTagInput('');
    setError('');
  }, [isOpen]);

  const handleSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    const tags = Array.from(new Set(tagInput.split(',').map((tag) => tag.trim()).filter(Boolean)));
    if (operation !== 'replace' && tags.length === 0) {
      setError('请输入至少一个标签');
      return;
    }
    onSubmit(operation, tags);
  };

  return (
    <Dialog open={isOpen} onOpenChange={(open) => !open && !isSubmitting && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Tags className="h-5 w-5 text-primary" />
            批量编辑设备标签
          </DialogTitle>
          <DialogDescription>
            将对选中的 {selectedCount} 台设备执行标签操作。
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label htmlFor="device-tag-operation" className={FORM.label}>操作方式</label>
            <select
              id="device-tag-operation"
              value={operation}
              onChange={(event) => {
                setOperation(event.target.value as DeviceTagOperation);
                setError('');
              }}
              className={FORM.input}
              disabled={isSubmitting}
            >
              <option value="add">添加标签（保留已有标签）</option>
              <option value="remove">移除指定标签</option>
              <option value="replace">替换全部标签</option>
            </select>
          </div>

          <div>
            <label htmlFor="device-bulk-tags-input" className={FORM.label}>标签</label>
            <input
              id="device-bulk-tags-input"
              value={tagInput}
              onChange={(event) => {
                setTagInput(event.target.value);
                setError('');
              }}
              placeholder="例如：shanghai、regression、android15（逗号分隔）"
              className={FORM.input}
              disabled={isSubmitting}
              autoFocus
            />
            {error ? (
              <p className={FORM.error}>{error}</p>
            ) : (
              <p className={FORM.hint}>
                {operation === 'replace' ? '留空并提交将清除全部标签。' : '多个标签使用英文逗号分隔。'}
              </p>
            )}
          </div>

          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose} disabled={isSubmitting}>
              取消
            </Button>
            <Button type="submit" disabled={isSubmitting}>
              {isSubmitting ? '更新中…' : '确认更新'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
