import { useState } from 'react';
import { Pencil } from 'lucide-react';
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
import type { ProjectDetail, ProjectUpdateInput } from '@/utils/api/types';

type Props = {
  isOpen: boolean;
  isSubmitting?: boolean;
  /** 打开时用于预填；提交只回传后端 _UPDATABLE_FIELDS 覆盖的六个字段 */
  project: ProjectDetail;
  onClose: () => void;
  onSubmit: (payload: ProjectUpdateInput) => void;
  /** ADR-0029 D2 复核：项目重命名（admin 传入时显示 key 输入框）。 */
  onRename?: (newKey: string) => void;
};

const EDITABLE_TEXT_FIELDS = [
  ['customer', '客户'],
  ['product_line', '产品线'],
] as const;

export default function EditProjectDialog({
  isOpen,
  isSubmitting = false,
  project,
  onClose,
  onSubmit,
  onRename,
}: Props) {
  const [form, setForm] = useState<ProjectUpdateInput>({});
  const [projectKey, setProjectKey] = useState('');
  const [error, setError] = useState('');

  const [prevOpen, setPrevOpen] = useState(isOpen);
  if (prevOpen !== isOpen) {
    setPrevOpen(isOpen);
    if (isOpen) {
      setProjectKey(project.project_key);
      setForm({
        display_name: project.display_name,
        customer: project.customer ?? '',
        form_factor: project.form_factor ?? '',
        product_line: project.product_line ?? '',
        jira_project_key: project.jira_project_key ?? '',
      });
      setError('');
    }
  }

  const setField = (key: keyof ProjectUpdateInput, value: string) => {
    setForm((prev) => ({ ...prev, [key]: value }));
    setError('');
  };

  const handleSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    const displayName = (form.display_name ?? '').trim();
    if (!displayName) {
      setError('显示名不能为空');
      return;
    }
    // 空串 → null：与后端 PUT fields_set 语义一致（显式清空 facet / jira 键）
    const blankToNull = (value?: string | null) => {
      const trimmed = value?.trim();
      return trimmed ? trimmed : null;
    };
    if (onRename && projectKey.trim() !== project.project_key) {
      onRename(projectKey.trim());
      return;
    }
    onSubmit({
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
            <Pencil className="h-5 w-5 text-primary" />
            编辑项目
          </DialogTitle>
          <DialogDescription>
            项目标识不可修改。字段变更逐项落审计（仅管理员可操作），JIRA 项目键
            将在 plan_run 源提单时自动带出。
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="space-y-3">
          {onRename ? (
            <div>
              <label className={FORM.label} htmlFor="edit-project-key">
                项目标识（key）
              </label>
              <Input
                id="edit-project-key"
                data-testid="edit-project-key"
                value={projectKey}
                onChange={(event) => setProjectKey(event.target.value)}
                className="font-mono"
              />
              <p className="mt-1 text-[11px] text-muted-foreground">
                改名后旧链接失效（外键不受影响），审计记录新旧 key
              </p>
            </div>
          ) : null}
          <div>
            <label className={FORM.label} htmlFor="edit-project-name">
              显示名
            </label>
            <Input
              id="edit-project-name"
              data-testid="edit-project-name"
              value={form.display_name ?? ''}
              onChange={(event) => setField('display_name', event.target.value)}
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            {EDITABLE_TEXT_FIELDS.map(([field, label]) => (
              <div key={field}>
                <label className={FORM.label} htmlFor={`edit-project-${field}`}>
                  {label}
                </label>
                <Input
                  id={`edit-project-${field}`}
                  data-testid={`edit-project-${field}`}
                  value={form[field] ?? ''}
                  onChange={(event) => setField(field, event.target.value)}
                />
              </div>
            ))}
            <div>
              <label className={FORM.label} htmlFor="edit-project-form">
                形态
              </label>
              <select
                id="edit-project-form"
                data-testid="edit-project-form"
                value={form.form_factor ?? ''}
                onChange={(event) => setField('form_factor', event.target.value || '')}
                className={FORM.select}
              >
                <option value="">未设置</option>
                <option value="PHONE">手机</option>
                <option value="TABLET">平板</option>
                <option value="WATCH">手表</option>
                <option value="OTHER">其他</option>
              </select>
            </div>
          </div>
          <div>
            <label className={FORM.label} htmlFor="edit-project-jira">
              JIRA 项目键
            </label>
            <Input
              id="edit-project-jira"
              data-testid="edit-project-jira"
              value={form.jira_project_key ?? ''}
              onChange={(event) => setField('jira_project_key', event.target.value)}
              placeholder="如 VFFCA；留空表示未配置"
              className="font-mono"
            />
          </div>
          {error ? (
            <p className="text-sm text-destructive" role="alert">
              {error}
            </p>
          ) : null}
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              disabled={isSubmitting}
              onClick={() => onClose()}
            >
              取消
            </Button>
            <Button type="submit" disabled={isSubmitting}>
              {isSubmitting ? '保存中…' : '保存'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
