import { useState } from 'react';
import { Layers } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { FORM } from '@/design-system';
import type { TestSuiteCreateInput } from '@/utils/api/types';

type Props = {
  isOpen: boolean;
  isSubmitting?: boolean;
  onClose: () => void;
  onSubmit: (payload: TestSuiteCreateInput) => void;
};

const EMPTY: TestSuiteCreateInput = {
  name: '',
  display_name: '',
  project_key: '',
  export_dir: '',
};

export default function CreateSuiteDialog({
  isOpen,
  isSubmitting = false,
  onClose,
  onSubmit,
}: Props) {
  const [form, setForm] = useState<TestSuiteCreateInput>(EMPTY);
  const [error, setError] = useState('');

  const [prevOpen, setPrevOpen] = useState(isOpen);
  if (prevOpen !== isOpen) {
    setPrevOpen(isOpen);
    if (isOpen) {
      setForm(EMPTY);
      setError('');
    }
  }

  const setField = (key: keyof TestSuiteCreateInput, value: string) => {
    setForm((prev) => ({ ...prev, [key]: value }));
    setError('');
  };

  const blankToNull = (value?: string | null) => {
    const trimmed = value?.trim();
    return trimmed ? trimmed : null;
  };

  const handleSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    const name = form.name.trim();
    if (!name) {
      setError('请填写套件名称');
      return;
    }
    onSubmit({
      name,
      display_name: blankToNull(form.display_name),
      project_key: blankToNull(form.project_key),
      export_dir: blankToNull(form.export_dir),
    });
  };

  return (
    <Dialog open={isOpen} onOpenChange={(open) => !open && !isSubmitting && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Layers className="h-5 w-5 text-primary" />
            新建用例套件
          </DialogTitle>
          <DialogDescription>
            套件名是 Plan 绑定的稳定引用（`suite_name`）。导入 runtask.xml 可批量填充用例。
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="space-y-3">
          <div>
            <label className={FORM.label} htmlFor="suite-name">套件名称</label>
            <Input
              id="suite-name"
              data-testid="create-suite-name"
              value={form.name}
              onChange={(e) => setField('name', e.target.value)}
              placeholder="如 mtbf_default"
              disabled={isSubmitting}
            />
          </div>
          <div>
            <label className={FORM.label} htmlFor="suite-display-name">显示名（可选）</label>
            <Input
              id="suite-display-name"
              value={form.display_name ?? ''}
              onChange={(e) => setField('display_name', e.target.value)}
              disabled={isSubmitting}
            />
          </div>
          <div>
            <label className={FORM.label} htmlFor="suite-project-key">项目标识（可选）</label>
            <Input
              id="suite-project-key"
              value={form.project_key ?? ''}
              onChange={(e) => setField('project_key', e.target.value)}
              placeholder="登记簿 project_key"
              disabled={isSubmitting}
            />
          </div>
          <div>
            <label className={FORM.label} htmlFor="suite-export-dir">导出目录（可选）</label>
            <Input
              id="suite-export-dir"
              value={form.export_dir ?? ''}
              onChange={(e) => setField('export_dir', e.target.value)}
              placeholder="工具目录相对路径"
              disabled={isSubmitting}
            />
          </div>
          {error && <p className="text-sm text-destructive">{error}</p>}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose} disabled={isSubmitting}>
              取消
            </Button>
            <Button type="submit" data-testid="create-suite-submit" disabled={isSubmitting}>
              创建
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
