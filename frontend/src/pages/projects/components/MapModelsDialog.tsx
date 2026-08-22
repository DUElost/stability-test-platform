import { useState } from 'react';
import { Link2 } from 'lucide-react';
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
import type { ProjectMapPreview, ProjectSummary } from '@/utils/api/types';

type Props = {
  isOpen: boolean;
  models: string[];
  projects: ProjectSummary[];
  preview: ProjectMapPreview | null;
  isPreviewing?: boolean;
  isSubmitting?: boolean;
  onClose: () => void;
  onInvalidatePreview: () => void;
  onPreview: (projectKey: string, reassignConflicts: boolean) => void;
  onApply: (projectKey: string, reassignConflicts: boolean) => void;
};

export default function MapModelsDialog({
  isOpen,
  models,
  projects,
  preview,
  isPreviewing = false,
  isSubmitting = false,
  onClose,
  onInvalidatePreview,
  onPreview,
  onApply,
}: Props) {
  const [projectKey, setProjectKey] = useState('');
  const [reassignConflicts, setReassignConflicts] = useState(false);
  const [error, setError] = useState('');

  const [prevOpen, setPrevOpen] = useState(isOpen);
  if (prevOpen !== isOpen) {
    setPrevOpen(isOpen);
    if (isOpen) {
      setProjectKey(projects[0]?.project_key ?? '');
      setReassignConflicts(false);
      setError('');
    }
  }

  const busy = isPreviewing || isSubmitting;

  return (
    <Dialog open={isOpen} onOpenChange={(open) => !open && !busy && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Link2 className="h-5 w-5 text-primary" />
            映射所选型号
          </DialogTitle>
          <DialogDescription>
            将 {models.join('、') || '所选型号'} 精确映射到人工项目。SEED / LEGACY / 空归属不算冲突。
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3 py-1">
          {projects.length === 0 ? (
            <p className={FORM.error}>请先新建一个项目，再映射型号。</p>
          ) : (
            <Select value={projectKey} onValueChange={(value) => {
              setProjectKey(value);
              setError('');
              onInvalidatePreview();
            }}>
              <SelectTrigger data-testid="map-project-select">
                <SelectValue placeholder="选择目标项目" />
              </SelectTrigger>
              <SelectContent>
                {projects.map((project) => (
                  <SelectItem key={project.project_key} value={project.project_key}>
                    {project.display_name}（{project.project_key}）
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              className="accent-primary"
              data-testid="map-reassign-conflicts"
              checked={reassignConflicts}
              onChange={(event) => {
                setReassignConflicts(event.target.checked);
                onInvalidatePreview();
              }}
            />
            覆盖已属于其他人工项目的设备
          </label>
          {preview ? (
            <div className="rounded-md border px-3 py-2 text-sm" data-testid="map-preview">
              <p>将归入 {preview.will_assign} 台 · 已在目标项目 {preview.already_in_target} 台</p>
              {preview.will_assign > 0 && (
                <p className="mt-1 text-amber-700 dark:text-warning">
                  将改归属 {preview.will_assign} 台设备；当前属 SEED / LEGACY 项目的设备不视为冲突，会直接迁入且不出现在冲突列表。
                </p>
              )}
              {preview.conflicts.length > 0 ? (
                <p className="mt-1 text-amber-700">
                  冲突 {preview.conflicts.length} 台
                  （{preview.conflicts.map((item) => item.serial).slice(0, 5).join('、')}
                  {preview.conflicts.length > 5 ? '…' : ''}）
                </p>
              ) : null}
              {preview.unknown_models.length > 0 ? (
                <p className="mt-1 text-amber-700 dark:text-warning" data-testid="map-unknown-models">
                  应用会把 {preview.unknown_models.length} 个当前 fleet 未见过的型号写入该项目的 match_models（不会被阻断）：{preview.unknown_models.join('、')}
                </p>
              ) : null}
            </div>
          ) : null}
          {error ? <p className={FORM.error}>{error}</p> : null}
        </div>
        <DialogFooter>
          <Button type="button" variant="ghost" onClick={onClose} disabled={busy}>
            取消
          </Button>
          <Button
            type="button"
            variant="outline"
            data-testid="map-preview-btn"
            disabled={busy || !projectKey}
            onClick={() => {
              if (!projectKey) {
                setError('请选择目标项目');
                return;
              }
              onPreview(projectKey, reassignConflicts);
            }}
          >
            {isPreviewing ? '预览中…' : '预览'}
          </Button>
          <Button
            type="button"
            data-testid="map-apply-btn"
            disabled={
              busy
              || !projectKey
              || !preview
              || (preview.conflicts.length > 0 && !reassignConflicts)
            }
            onClick={() => {
              if (!projectKey) {
                setError('请选择目标项目');
                return;
              }
              onApply(projectKey, reassignConflicts);
            }}
          >
            {isSubmitting ? '应用中…' : '应用'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
