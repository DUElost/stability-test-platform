import { useState } from 'react';
import { FolderKanban } from 'lucide-react';
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
import type { ProjectCreateInput } from '@/utils/api/types';

type Props = {
  isOpen: boolean;
  isSubmitting?: boolean;
  onClose: () => void;
  onSubmit: (payload: ProjectCreateInput) => void;
};

const EMPTY: ProjectCreateInput = {
  project_key: '',
  display_name: '',
  customer: '',
  form_factor: '',
  product_line: '',
  jira_project_key: '',
};

export default function CreateProjectDialog({
  isOpen,
  isSubmitting = false,
  onClose,
  onSubmit,
}: Props) {
  const [form, setForm] = useState<ProjectCreateInput>(EMPTY);
  const [error, setError] = useState('');

  const [prevOpen, setPrevOpen] = useState(isOpen);
  if (prevOpen !== isOpen) {
    setPrevOpen(isOpen);
    if (isOpen) {
      setForm(EMPTY);
      setError('');
    }
  }

  const setField = (key: keyof ProjectCreateInput, value: string) => {
    setForm((prev) => ({ ...prev, [key]: value }));
    setError('');
  };

  const handleSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    const projectKey = form.project_key.trim();
    const displayName = form.display_name.trim();
    if (!projectKey || !displayName) {
      setError('请填写项目标识和显示名');
      return;
    }
    if (!/^[A-Za-z0-9][A-Za-z0-9-]{0,62}$/.test(projectKey)) {
      setError('项目标识仅允许字母、数字和连字符');
      return;
    }
    const blankToNull = (value?: string | null) => {
      const trimmed = value?.trim();
      return trimmed ? trimmed : null;
    };
    onSubmit({
      project_key: projectKey,
      display_name: displayName,
      customer: blankToNull(form.customer),
      form_factor: blankToNull(form.form_factor),
      product_line: blankToNull(form.product_line),
      jira_project_key: blankToNull(form.jira_project_key),
    });
  };

  return (
    <Dialog open={isOpen} onOpenChange={(open) => !open && !isSubmitting && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <FolderKanban className="h-5 w-5 text-primary" />
            新建项目
          </DialogTitle>
          <DialogDescription>
            项目是知识层登记（客户 / 形态 / JIRA），不是从设备型号自动推断出来的。
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="space-y-3">
          <div>
            <label className={FORM.label} htmlFor="project-key">
              项目标识
            </label>
            <Input
              id="project-key"
              data-testid="create-project-key"
              value={form.project_key}
              onChange={(event) => setField('project_key', event.target.value)}
              placeholder="HONOR-CAMERA"
            />
          </div>
          <div>
            <label className={FORM.label} htmlFor="project-name">
              显示名
            </label>
            <Input
              id="project-name"
              data-testid="create-project-name"
              value={form.display_name}
              onChange={(event) => setField('display_name', event.target.value)}
              placeholder="荣耀相机"
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className={FORM.label} htmlFor="project-customer">
                客户
              </label>
              <Input
                id="project-customer"
                value={form.customer ?? ''}
                onChange={(event) => setField('customer', event.target.value)}
              />
            </div>
            <div>
              <label className={FORM.label} htmlFor="project-form">
                形态
              </label>
              <select
                id="project-form"
                data-testid="create-project-form"
                value={form.form_factor ?? ''}
                onChange={(event) => setField('form_factor', event.target.value)}
                className={FORM.select}
              >
                <option value="">未设置</option>
                <option value="PHONE">手机</option>
                <option value="TABLET">平板</option>
                <option value="WATCH">手表</option>
                <option value="OTHER">其他</option>
              </select>
            </div>
            <div>
              <label className={FORM.label} htmlFor="project-line">
                产品线
              </label>
              <Input
                id="project-line"
                value={form.product_line ?? ''}
                onChange={(event) => setField('product_line', event.target.value)}
              />
            </div>
          </div>
          <div>
            <label className={FORM.label} htmlFor="project-jira">
              JIRA 项目关键字
            </label>
            <Input
              id="project-jira"
              value={form.jira_project_key ?? ''}
              onChange={(event) => setField('jira_project_key', event.target.value)}
            />
          </div>
          {error ? <p className={FORM.error}>{error}</p> : null}
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={onClose} disabled={isSubmitting}>
              取消
            </Button>
            <Button type="submit" data-testid="create-project-confirm" disabled={isSubmitting}>
              {isSubmitting ? '创建中…' : '创建'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
