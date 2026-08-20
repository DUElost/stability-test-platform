import { useState, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { planKeys } from '@/utils/api/queryKeys';
import { useToast } from '@/hooks/useToast';
import { useConfirm } from '@/hooks/useConfirm';
import { useDocumentTitle } from '@/hooks/useDocumentTitle';
import { api, toApiError, type Plan } from '@/utils/api';
import { PageSkeleton } from '@/components/ui/loading-skeleton';
import { EmptyState, SearchEmptyState } from '@/components/ui/empty-state';
import { ErrorState } from '@/components/ui/error-state';
import { Plus, Edit, Trash2, Search, FileText, Play } from 'lucide-react';
import { PageContainer, PageHeader } from '@/components/layout';
import { Badge } from '@/components/ui/badge';
import { ProjectFilterSelect, ProjectKeyBadge } from '@/components/project/ProjectFilterSelect';
import { STAT, TEXT } from '@/design-system/tokens';
import { cn } from '@/lib/utils';
import { formatLocalDate } from '@/utils/format';

export default function PlanListPage() {
  useDocumentTitle('Plan 编排');
  const navigate = useNavigate();
  const toast = useToast();
  const confirmDialog = useConfirm();
  const queryClient = useQueryClient();
  const [search, setSearch] = useState('');
  // ADR-0029：页面级项目筛选（无全局选择器/跨页跟随）
  const [projectKey, setProjectKey] = useState<string | undefined>(undefined);

  const {
    data: plans,
    isLoading,
    isError,
    error,
    refetch,
  } = useQuery({
    queryKey: planKeys.list(100, projectKey),
    queryFn: () => api.plans.list(0, 100, projectKey),
  });

  const isProject404 = isError && toApiError(error).status === 404;

  const deleteMutation = useMutation({
    mutationFn: (plan: Plan) => api.plans.delete(plan.id, plan.updated_at),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: planKeys.allLists() });
      toast.success('Plan 已删除');
    },
    onError: (err: unknown) => toast.error(toApiError(err).message),
  });

  const filtered = useMemo(() => {
    if (!plans) return [];
    const q = search.toLowerCase();
    return plans.filter(p =>
      !q || p.name.toLowerCase().includes(q) || (p.description || '').toLowerCase().includes(q)
    );
  }, [plans, search]);

  const handleDelete = async (plan: Plan) => {
    const ok = await confirmDialog({
      title: '删除 Plan',
      description: `确定删除 "${plan.name}"？此操作不可撤销。`,
      variant: 'destructive',
    });
    if (ok) deleteMutation.mutate(plan);
  };

  const stats = useMemo(() => ({
    total: plans?.length ?? 0,
    withSteps: plans?.filter(p => p.steps?.length > 0).length ?? 0,
    chained: plans?.filter(p => p.next_plan_id != null).length ?? 0,
  }), [plans]);

  return (
    <PageContainer width="list">
      <PageHeader
        title="Plan 编排"
        subtitle="基于 Plan-Step 模型管理测试编排，支持链接式 Plan 链"
        action={
          <ProjectFilterSelect
            value={projectKey}
            onChange={setProjectKey}
            className="w-52"
            testId="plan-project-filter"
          />
        }
      />

      {/* Stats */}
      <div className="grid grid-cols-3 gap-4">
        <Card>
          <CardContent className="py-4 text-center">
            <p className={STAT.value}>{stats.total}</p>
            <p className={STAT.label}>Plan 总数</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="py-4 text-center">
            <p className={STAT.value}>{stats.withSteps}</p>
            <p className={STAT.label}>已配置步骤</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="py-4 text-center">
            <p className={STAT.value}>{stats.chained}</p>
            <p className={STAT.label}>链式 Plan</p>
          </CardContent>
        </Card>
      </div>

      {/* Search + Create */}
      <div className="flex items-center gap-3">
        <div className="relative flex-1">
          <Search className={cn('absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4', TEXT.subtitle)} />
          <Input
            type="text" placeholder="搜索 Plan 名称或描述..." value={search}
            onChange={e => setSearch(e.target.value)}
            className="pl-9"
          />
        </div>
        <Button onClick={() => navigate('/orchestration/plans/new')}>
          <Plus className="w-4 h-4 mr-1.5" /> 新建 Plan
        </Button>
      </div>

      {/* List */}
      {isLoading ? (
        <PageSkeleton>
          <PageSkeleton.Cards count={3} />
        </PageSkeleton>
      ) : isError ? (
        <ErrorState
          // 未知项目 key：按错误态渲染（后端统一 404），不吞成空列表
          title={isProject404 ? '项目不存在' : '加载 Plan 列表失败'}
          description={isProject404
            ? `项目 "${projectKey}" 不存在，请清除筛选或核对 key`
            : (error as Error)?.message || '请检查网络连接或稍后重试'}
          onRetry={isProject404 ? undefined : () => void refetch()}
          action={isProject404 ? (
            <Button variant="outline" onClick={() => setProjectKey(undefined)}>
              清除项目筛选
            </Button>
          ) : undefined}
        />
      ) : filtered.length === 0 ? (
        search ? (
          <SearchEmptyState keyword={search} />
        ) : (
          <EmptyState
            title="还没有 Plan"
            description="创建您的第一个测试计划"
            icon={<FileText className="w-16 h-16" />}
            action={
              <Button onClick={() => navigate('/orchestration/plans/new')}>
                <Plus className="w-4 h-4 mr-2" /> 新建 Plan
              </Button>
            }
          />
        )
      ) : (
        <div className="space-y-3">
          {filtered.map(plan => (
            <Card key={plan.id} className="group hover:shadow-md transition-shadow">
              <CardContent className="py-4 flex items-center justify-between">
                <div className="min-w-0 flex-1 space-y-3">
                  <div className="flex items-center gap-2">
                    <h3 className={cn('font-medium truncate', TEXT.heading)}>{plan.name}</h3>
                    <ProjectKeyBadge projectKey={plan.project_key} />
                    {plan.next_plan_id != null && (
                      <Badge variant="info" className="text-xs px-1.5 py-0.5">链式</Badge>
                    )}
                  </div>
                  {plan.description && (
                    <p className={cn('text-sm truncate', TEXT.subtitle)}>{plan.description}</p>
                  )}
                  <div className={cn('flex items-center gap-3 text-xs pt-1', TEXT.subtitle)}>
                    <span>{plan.steps?.length ?? 0} 步骤</span>
                    <span>阈值 {Math.round((plan.failure_threshold ?? 0.05) * 100)}%</span>
                    {plan.created_by && <span>创建者: {plan.created_by}</span>}
                    <span>更新于 {formatLocalDate(plan.updated_at)}</span>
                  </div>
                </div>
                <div className="flex items-center gap-1 ml-4 opacity-100 md:opacity-0 md:group-hover:opacity-100 transition-opacity">
                  <Button variant="ghost" size="sm" onClick={() => navigate(`/execution/plan-execute?plan=${plan.id}`)} title="执行">
                    <Play className="w-4 h-4" />
                  </Button>
                  <Button variant="ghost" size="sm" onClick={() => navigate(`/orchestration/plans/${plan.id}`)} title="编辑">
                    <Edit className="w-4 h-4" />
                  </Button>
                  <Button variant="ghost" size="sm" onClick={() => handleDelete(plan)} className={cn(TEXT.destructive, 'hover:text-destructive')} title="删除">
                    <Trash2 className="w-4 h-4" />
                  </Button>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </PageContainer>
  );
}
