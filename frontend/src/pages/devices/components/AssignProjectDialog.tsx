import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
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
import { FORM } from '@/design-system';
import { api } from '@/utils/api';
import { projectKeys } from '@/utils/api/queryKeys';

interface AssignProjectDialogProps {
  isOpen: boolean;
  selectedCount: number;
  isSubmitting?: boolean;
  onClose: () => void;
  onSubmit: (projectKey: string) => void;
}

/**
 * ADR-0029 P2 — 设备批量归入项目（admin 动作，后端 POST /devices/bulk-project）。
 * 幂等：已是目标项目的设备跳过。下拉只含人工 USER 项目。
 */
export function AssignProjectDialog({
  isOpen,
  selectedCount,
  isSubmitting = false,
  onClose,
  onSubmit,
}: AssignProjectDialogProps) {
  const [projectKey, setProjectKey] = useState('');
  const [error, setError] = useState('');

  const { data: projects } = useQuery({
    queryKey: projectKeys.list(),
    queryFn: () => api.projects.list(),
  });

  const [prevOpen, setPrevOpen] = useState(isOpen);
  if (prevOpen !== isOpen) {
    setPrevOpen(isOpen);
    if (isOpen) {
      setProjectKey('');
      setError('');
    }
  }

  const handleSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    if (!projectKey) {
      setError('请选择目标项目');
      return;
    }
    onSubmit(projectKey);
  };

  return (
    <Dialog open={isOpen} onOpenChange={(open) => !open && !isSubmitting && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <FolderKanban className="h-5 w-5 text-primary" />
            批量归入项目
          </DialogTitle>
          <DialogDescription>
            将 {selectedCount} 台设备归入所选项目。已在该项目的设备自动跳过（幂等），
            归入操作记录审计日志。
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSubmit}>
          <div className="space-y-4 py-2">
            <select
              value={projectKey}
              onChange={(e) => { setProjectKey(e.target.value); setError(''); }}
              data-testid="assign-project-select"
              className={FORM.select}
            >
              <option value="" disabled>选择目标项目</option>
              {projects?.map((project) => (
                <option key={project.project_key} value={project.project_key}>
                  {project.display_name}（{project.project_key}）
                </option>
              ))}
            </select>
            {error && <p className={FORM.error}>{error}</p>}
            <p className="rounded-md bg-warning/10 px-3 py-2 text-sm text-warning" data-testid="assign-seed-notice">
              所选 {selectedCount} 台设备的归属将改为目标项目；当前属于 SEED / LEGACY
              项目的设备会被直接迁移，不会出现冲突确认。
            </p>
          </div>
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={onClose} disabled={isSubmitting}>
              取消
            </Button>
            <Button type="submit" data-testid="assign-project-confirm" disabled={isSubmitting}>
              {isSubmitting ? '归入中…' : '确认归入'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
