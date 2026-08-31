import { useEffect, useState } from 'react';
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
import { api } from '@/utils/api';
import type { CustomerDict, ProjectCreateInput } from '@/utils/api/types';

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
  // ADR-0029 D12：customer 字典下拉建议（静态数据，挂载时取一次；
  // 失败降级纯输入框，不阻断编辑）
  const [customers, setCustomers] = useState<CustomerDict[]>([]);

  useEffect(() => {
    let cancelled = false;
    api.projects
      .customers()
      .then((rows) => {
        if (!cancelled) setCustomers(rows);
      })
      .catch(() => {
        /* 字典不可用 = 保持自由文本输入 */
      });
    return () => {
      cancelled = true;
    };
  }, []);

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
                data-testid="create-project-customer"
                value={form.customer ?? ''}
                onChange={(event) => setField('customer', event.target.value)}
                list="create-project-customer-options"
              />
              <datalist id="create-project-customer-options">
                {customers.map((c) => (
                  <option key={c.key} value={c.display_name} />
                ))}
              </datalist>
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
