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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
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
            <Select value={projectKey} onValueChange={(v) => { setProjectKey(v); setError(''); }}>
              <SelectTrigger data-testid="assign-project-select">
                <SelectValue placeholder="选择目标项目" />
              </SelectTrigger>
              <SelectContent>
                {projects?.map((project) => (
                  <SelectItem key={project.project_key} value={project.project_key}>
                    {project.display_name}（{project.project_key}）
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {error && <p className={FORM.error}>{error}</p>}
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
