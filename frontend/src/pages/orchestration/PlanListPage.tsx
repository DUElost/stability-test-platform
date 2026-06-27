import { useState, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { planKeys } from '@/utils/api/queryKeys';
import { useToast } from '@/hooks/useToast';
import { useConfirm } from '@/hooks/useConfirm';
import { api, type Plan } from '@/utils/api';
import { Badge } from '@/components/ui/badge';
import { PageContainer, PageHeaderV2 } from '@/components/layout';
import { DataList, DataListItem, DataToolbar, DataEmptyState } from '@/components/data';
import { STAT, TEXT } from '@/design-system/tokens';
import { cn } from '@/lib/utils';
import { formatLocalDate } from '@/utils/format';
import { Plus, Edit, Play, LayoutGrid, List, FileText } from 'lucide-react';

type ViewMode = 'grid' | 'list';

export default function PlanListPage() {
  const navigate = useNavigate();
  const toast = useToast();
  const confirmDialog = useConfirm();
  const queryClient = useQueryClient();
  const [search, setSearch] = useState('');
  const [view, setView] = useState<ViewMode>('grid');

  const { data: plans, isLoading } = useQuery({
    queryKey: planKeys.list(100),
    queryFn: () => api.plans.list(0, 100),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.plans.delete(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: planKeys.allLists() });
      toast.success('Plan 已删除');
    },
    onError: (err: any) => toast.error(err.message || '删除失败'),
  });

  const filtered = useMemo(() => {
    if (!plans) return [];
    const q = search.toLowerCase();
    return plans.filter(
      (p) =>
        !q ||
        p.name.toLowerCase().includes(q) ||
        (p.description || '').toLowerCase().includes(q),
    );
  }, [plans, search]);

  const handleDelete = async (plan: Plan) => {
    const ok = await confirmDialog({
      title: '删除 Plan',
      description: `确定删除 "${plan.name}"？此操作不可撤销。`,
      variant: 'destructive',
    });
    if (ok) deleteMutation.mutate(plan.id);
  };

  const stats = useMemo(
    () => ({
      total: plans?.length ?? 0,
      withSteps: plans?.filter((p) => p.steps?.length > 0).length ?? 0,
      chained: plans?.filter((p) => p.next_plan_id != null).length ?? 0,
    }),
    [plans],
  );

  const renderPlanItem = (plan: Plan) => {
    const content = (
      <div className="min-w-0 flex-1 space-y-2">
        <div className="flex items-center gap-2">
          <h3 className={cn('font-medium truncate', TEXT.heading)}>{plan.name}</h3>
          {plan.next_plan_id != null && (
            <Badge variant="info" className="text-xs px-1.5 py-0.5">
              链式
            </Badge>
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
    );

    return (
      <DataListItem
        onNavigate={() => navigate(`/orchestration/plans/${plan.id}`)}
        actions={
          <>
            <Button
              variant="ghost"
              size="sm"
              onClick={(e) => {
                e.stopPropagation();
                navigate(`/execution/plan-execute?plan=${plan.id}`);
              }}
              aria-label="执行"
            >
              <Play className="w-4 h-4" />
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={(e) => {
                e.stopPropagation();
                navigate(`/orchestration/plans/${plan.id}`);
              }}
              aria-label="编辑"
            >
              <Edit className="w-4 h-4" />
            </Button>
          </>
        }
        moreActions={[
          {
            label: '删除',
            onClick: () => handleDelete(plan),
            destructive: true,
          },
        ]}
      >
        {view === 'grid' ? (
          <div className="p-1">{content}</div>
        ) : (
          <div className="flex items-center justify-between w-full">{content}</div>
        )}
      </DataListItem>
    );
  };

  return (
    <PageContainer fullBleed>
      <PageHeaderV2
        title="Plan 编排"
        description="基于 Plan-Step 模型管理测试编排，支持链接式 Plan 链"
        actions={
          <Button onClick={() => navigate('/orchestration/plans/new')}>
            <Plus className="w-4 h-4 mr-1.5" /> 新建 Plan
          </Button>
        }
      />

      <div className="grid grid-cols-3 gap-4 px-6">
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

      <div className="px-6 pb-2">
        <DataToolbar
          searchValue={search}
          onSearchChange={setSearch}
          searchPlaceholder="搜索 Plan 名称或描述..."
        >
          <Button
            variant={view === 'grid' ? 'secondary' : 'ghost'}
            size="icon"
            onClick={() => setView('grid')}
            aria-label="网格视图"
          >
            <LayoutGrid className="w-4 h-4" />
          </Button>
          <Button
            variant={view === 'list' ? 'secondary' : 'ghost'}
            size="icon"
            onClick={() => setView('list')}
            aria-label="列表视图"
          >
            <List className="w-4 h-4" />
          </Button>
        </DataToolbar>
      </div>

      <div className="px-6 pb-6 flex-1">
        <DataList
          items={filtered}
          isLoading={isLoading}
          keyExtractor={(plan) => String(plan.id)}
          renderItem={(plan) => renderPlanItem(plan)}
          emptyState={
            <DataEmptyState
              title="还没有 Plan"
              description="创建您的第一个测试计划"
              icon={<FileText className="w-16 h-16" />}
              action={
                <Button onClick={() => navigate('/orchestration/plans/new')}>
                  <Plus className="w-4 h-4 mr-2" /> 新建 Plan
                </Button>
              }
            />
          }
        />
      </div>
    </PageContainer>
  );
}
