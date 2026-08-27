import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Pencil } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet';
import { Skeleton } from '@/components/ui/skeleton';
import { ErrorState } from '@/components/ui/error-state';
import { TEXT } from '@/design-system/tokens';
import { cn } from '@/lib/utils';
import { useAuthSession } from '@/hooks/useAuthSession';
import { useToast } from '@/hooks/useToast';
import { api, toApiError } from '@/utils/api';
import { projectKeys } from '@/utils/api/queryKeys';
import type { ProjectUpdateInput } from '@/utils/api/types';
import EditProjectDialog from './EditProjectDialog';

const FACET_FIELDS = [
  ['customer', '客户'],
  ['platform', '平台'],
  ['form_factor', '形态'],
  ['product_line', '产品线'],
] as const;

type Props = {
  projectKey: string | null;
  isOpen: boolean;
  onClose: () => void;
};

/** 方向 1 首个实践：列表行内「抽屉式」项目详情（渐进披露，不离开登记簿）。 */
export default function ProjectDetailSheet({ projectKey, isOpen, onClose }: Props) {
  const navigate = useNavigate();
  const toast = useToast();
  const queryClient = useQueryClient();
  const sessionQ = useAuthSession();
  const isAdmin = sessionQ.data?.role === 'admin';
  const [editOpen, setEditOpen] = useState(false);

  const detailQ = useQuery({
    // projectKey 变化即视为新内容；关闭态不发请求
    queryKey: ['project-sheet', projectKey],
    queryFn: () => api.projects.get(projectKey as string),
    enabled: isOpen && !!projectKey,
  });

  const updateMutation = useMutation({
    mutationFn: (payload: ProjectUpdateInput) =>
      api.projects.update(projectKey as string, payload),
    onSuccess: () => {
      toast.success('项目信息已更新');
      setEditOpen(false);
      void queryClient.invalidateQueries({ queryKey: ['project-sheet', projectKey] });
      void queryClient.invalidateQueries({ queryKey: projectKeys.list() });
      if (projectKey) {
        void queryClient.invalidateQueries({ queryKey: projectKeys.detail(projectKey) });
      }
    },
    onError: (error) => {
      toast.error(`更新失败: ${toApiError(error).message || '请稍后重试'}`);
    },
  });

  const project = detailQ.data;

  return (
    <Sheet open={isOpen} onOpenChange={(open) => !open && onClose()}>
      <SheetContent
        side="right"
        data-testid="project-detail-sheet"
        onOpenAutoFocus={(event) => event.preventDefault()}
      >
        <SheetHeader className="pr-8">
          {detailQ.isLoading ? (
            <>
              <Skeleton className="h-6 w-40" />
              <Skeleton className="h-4 w-24" />
            </>
          ) : project ? (
            <>
              <SheetTitle>{project.display_name}</SheetTitle>
              <SheetDescription className="font-mono">
                {project.project_key}
                {project.status === 'ARCHIVED' ? ' · 已归档' : ''}
              </SheetDescription>
            </>
          ) : null}
        </SheetHeader>

        <div className="-mx-6 flex-1 overflow-y-auto px-6">
          {detailQ.isError ? (
            <ErrorState
              title="加载项目失败"
              description={(detailQ.error as Error)?.message || '请稍后重试'}
              onRetry={() => void detailQ.refetch()}
            />
          ) : detailQ.isLoading || !project ? (
            <div className="space-y-3">
              <Skeleton className="h-16 w-full" />
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-24 w-full" />
            </div>
          ) : (
            <div className="space-y-4 pb-4">
              {/* KPI 行 */}
              <div className="grid grid-cols-3 divide-x rounded-lg border py-2.5 text-center">
                <div>
                  <p className={cn('text-lg font-bold leading-none', TEXT.heading)}>
                    {project.device_count}
                  </p>
                  <p className={cn('mt-1 text-[11px]', TEXT.subtitle)}>设备</p>
                </div>
                <div>
                  <p
                    className={cn(
                      'text-lg font-bold leading-none',
                      project.running_run_count > 0 ? 'text-success' : TEXT.subtitle,
                    )}
                  >
                    {project.running_run_count}
                  </p>
                  <p className={cn('mt-1 text-[11px]', TEXT.subtitle)}>在跑 Run</p>
                </div>
                <div>
                  <p className={cn('text-lg font-bold leading-none', TEXT.heading)}>
                    {project.plan_count}
                  </p>
                  <p className={cn('mt-1 text-[11px]', TEXT.subtitle)}>Plan</p>
                </div>
              </div>

              {/* facet 徽标 */}
              <div className="flex flex-wrap gap-1.5">
                {FACET_FIELDS.map(([field, label]) => {
                  const value = project[field];
                  if (!value) return null;
                  return (
                    <Badge key={field} variant="outline" className="text-[11px] font-normal">
                      {label}: {value}
                    </Badge>
                  );
                })}
                {project.source === 'SEED' ? (
                  <Badge variant="secondary" className="text-[11px] font-normal">
                    系统回填
                  </Badge>
                ) : null}
              </div>

              {/* JIRA 集成（抽屉内快捷修正通道） */}
              <div className="rounded-lg border p-3">
                <div className="flex items-center justify-between gap-2">
                  <p className="text-sm font-medium">JIRA 项目键</p>
                  {isAdmin && project.status !== 'ARCHIVED' ? (
                    <Button
                      size="sm"
                      variant="ghost"
                      data-testid="sheet-edit-jira-open"
                      onClick={() => setEditOpen(true)}
                    >
                      <Pencil className="h-3.5 w-3.5 mr-1" />
                      编辑
                    </Button>
                  ) : null}
                </div>
                {project.jira_project_key ? (
                  <p className={cn('mt-1 font-mono text-sm', TEXT.heading)}>
                    {project.jira_project_key}
                    <span className={cn('ml-2 font-sans text-xs', TEXT.subtitle)}>
                      plan_run 源提单自动带出
                    </span>
                  </p>
                ) : (
                  <p data-testid="sheet-jira-placeholder" className={cn('mt-1 text-xs', TEXT.subtitle)}>
                    未配置。{isAdmin ? '点上方「编辑」填写；' : ''}配置后提单自动带出。
                  </p>
                )}
              </div>

              {(project.match_models ?? []).length > 0 ? (
                <p
                  title={(project.match_models ?? []).join(' · ')}
                  className={cn('truncate font-mono text-xs', TEXT.subtitle)}
                >
                  已映射型号：{(project.match_models ?? []).join(' · ')}
                </p>
              ) : null}
            </div>
          )}
        </div>

        <SheetFooter>
          <Button
            variant="outline"
            disabled={!project}
            onClick={() => {
              onClose();
              navigate(`/projects/${projectKey}`);
            }}
            data-testid="sheet-open-full-page"
          >
            打开完整详情页
          </Button>
        </SheetFooter>
      </SheetContent>

      {isAdmin && project ? (
        <EditProjectDialog
          isOpen={editOpen}
          isSubmitting={updateMutation.isPending}
          project={project}
          onClose={() => setEditOpen(false)}
          onSubmit={(payload) => updateMutation.mutate(payload)}
        />
      ) : null}

    </Sheet>
  );
}
