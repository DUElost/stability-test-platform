import { useState, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
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
import { DashboardStatCard } from '@/components/dashboard/DashboardStatCard';
import { FORM } from '@/design-system';
import { LAYOUT, TEXT } from '@/design-system/tokens';
import { cn } from '@/lib/utils';
import { formatLocalDate } from '@/utils/format';

/** ADR-0029 D6（#448 半段）：专项筛选下拉，字典源 GET /specialties（与编辑器共用缓存）。 */
function SpecialtyFilterSelect({
  value,
  onChange,
}: {
  value: string | undefined;
  onChange: (value: string | undefined) => void;
}) {
  const { data: specialties } = useQuery({
    queryKey: ['specialties'],
    queryFn: () => api.plans.listSpecialties(),
  });

  return (
    <select
      value={value ?? 'all'}
      onChange={(e) => onChange(e.target.value === 'all' ? undefined : e.target.value)}
      className={`${FORM.select} w-36`}
      data-testid="plan-specialty-filter"
    >
      <option value="all">全部专项</option>
      {specialties?.map((s) => (
        <option key={s.key} value={s.key}>{s.display_name}</option>
      ))}
    </select>
  );
}

export default function PlanListPage() {
  useDocumentTitle('Plan 编排');
  const navigate = useNavigate();
  const toast = useToast();
  const confirmDialog = useConfirm();
  const queryClient = useQueryClient();
  const [search, setSearch] = useState('');
  // ADR-0029：页面级项目/专项筛选（无全局选择器/跨页跟随）
  const [projectKey, setProjectKey] = useState<string | undefined>(undefined);
  const [specialtyKey, setSpecialtyKey] = useState<string | undefined>(undefined);

  const {
    data: plans,
    isLoading,
    isError,
    error,
    refetch,
  } = useQuery({
    queryKey: planKeys.list(100, projectKey, specialtyKey),
    queryFn: () => api.plans.list(0, 100, projectKey, specialtyKey),
  });

  const isProject404 = isError && toApiError(error).status === 404;

  // D6：卡片徽章此前直接渲染 specialty_key 原始 key，而筛选下拉用 display_name，
  // 同一概念两处文案对不上。取同一份字典（与 SpecialtyFilterSelect 共用
  // ['specialties'] 缓存，不额外发请求）做展示名映射；字典未就绪时回落 key。
  const { data: specialties } = useQuery({
    queryKey: ['specialties'],
    queryFn: () => api.plans.listSpecialties(),
  });
  const specialtyLabel = useMemo(() => {
    const byKey = new Map((specialties ?? []).map((s) => [s.key, s.display_name]));
    return (key: string) => byKey.get(key) ?? key;
  }, [specialties]);

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

  // ADR-0029 D6（#448）：项目×专项二维分组——按 project_key 保序分组。
  const grouped = useMemo(() => {
    const groups = new Map<string, Plan[]>();
    for (const plan of filtered) {
      const key = plan.project_key || '未归属';
      const list = groups.get(key) ?? [];
      list.push(plan);
      groups.set(key, list);
    }
    return Array.from(groups.entries());
  }, [filtered]);

  return (
    <PageContainer width="content" className={LAYOUT.pageGap}>
      <PageHeader
        title="Plan 编排"
        subtitle="基于 Plan-Step 模型管理测试编排，支持链接式 Plan 链"
      />

      <div className="grid grid-cols-3 gap-4">
        <DashboardStatCard label="Plan 总数" value={stats.total} />
        <DashboardStatCard label="已配置步骤" value={stats.withSteps} />
        <DashboardStatCard label="链式 Plan" value={stats.chained} />
      </div>

      <div className="flex flex-col gap-3 lg:flex-row lg:items-center">
        <div className="relative min-w-0 flex-1">
          <Search className={cn('absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4', TEXT.subtitle)} />
          <Input
            type="search"
            placeholder="搜索 Plan 名称或描述..."
            value={search}
            onChange={e => setSearch(e.target.value)}
            className="pl-9"
            data-testid="plan-search"
          />
        </div>
        <div className="flex flex-wrap items-center gap-2 shrink-0">
          <ProjectFilterSelect
            value={projectKey}
            onChange={setProjectKey}
            className="w-44"
            testId="plan-project-filter"
          />
          <SpecialtyFilterSelect value={specialtyKey} onChange={setSpecialtyKey} />
          <Button onClick={() => navigate('/orchestration/plans/new')} className="shrink-0">
            <Plus className="w-4 h-4 mr-1.5" /> 新建 Plan
          </Button>
        </div>
      </div>

      {isLoading ? (
        <PageSkeleton>
          <PageSkeleton.Cards count={3} />
        </PageSkeleton>
      ) : isError ? (
        <ErrorState
          title={isProject404 ? '项目不存在' : '加载 Plan 列表失败'}
          description={isProject404
            ? `项目 "${projectKey}" 不存在，请清除筛选或核对 key`
            : toApiError(error).message || '请检查网络连接或稍后重试'}
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
        <div className="space-y-4">
          {grouped.map(([groupKey, groupPlans]) => (
            <section key={groupKey} className="space-y-2" data-testid={`plan-group-${groupKey}`}>
              <h3 className={cn('flex items-center gap-2 text-xs font-semibold uppercase tracking-wide', TEXT.subtitle)}>
                <ProjectKeyBadge projectKey={groupKey === '未归属' ? undefined : groupKey} />
                <span>{groupKey}</span>
                <span className="font-normal">（{groupPlans.length}）</span>
              </h3>
              <div className="overflow-x-auto rounded-lg border border-border">
                <Table className="min-w-[720px]">
                  <TableHeader>
                    <TableRow className="border-b text-left text-xs text-muted-foreground hover:bg-transparent">
                      <TableHead className="h-9 px-3">Plan</TableHead>
                      <TableHead className="h-9 px-3">专项</TableHead>
                      <TableHead className="h-9 px-3">步骤</TableHead>
                      <TableHead className="h-9 px-3">失败阈值</TableHead>
                      <TableHead className="h-9 px-3">更新</TableHead>
                      <TableHead className="h-9 px-3 text-right">操作</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {groupPlans.map((plan) => (
                      <TableRow
                        key={plan.id}
                        className="border-b last:border-0 hover:bg-muted/30"
                      >
                        <TableCell className="max-w-[280px] px-3 py-2.5">
                          <div className="flex min-w-0 flex-wrap items-center gap-1.5">
                            <span className={cn('truncate text-sm font-medium', TEXT.heading)}>
                              {plan.name}
                            </span>
                            {plan.suite_name && (
                              <Badge variant="info" className="text-xs px-1.5 py-0.5" title="托管模式：已绑定套件">
                                套件:{plan.suite_name}
                              </Badge>
                            )}
                            {plan.next_plan_id != null && (
                              <Badge variant="info" className="text-xs px-1.5 py-0.5">链式</Badge>
                            )}
                          </div>
                          {plan.description && (
                            <p className={cn('mt-0.5 truncate text-xs', TEXT.caption)}>
                              {plan.description}
                            </p>
                          )}
                        </TableCell>
                        <TableCell className="px-3 py-2.5">
                          {plan.specialty_key ? (
                            <Badge
                              variant="default"
                              className="text-xs px-1.5 py-0.5"
                              title={plan.specialty_key}
                            >
                              {specialtyLabel(plan.specialty_key)}
                            </Badge>
                          ) : (
                            <span className={cn('text-xs', TEXT.caption)}>—</span>
                          )}
                        </TableCell>
                        <TableCell className={cn('px-3 py-2.5 text-xs', TEXT.caption)}>
                          {plan.steps?.length ?? 0}
                        </TableCell>
                        <TableCell className={cn('px-3 py-2.5 text-xs', TEXT.caption)}>
                          {Math.round((plan.failure_threshold ?? 0.05) * 100)}%
                        </TableCell>
                        <TableCell className={cn('px-3 py-2.5 text-xs whitespace-nowrap', TEXT.caption)}>
                          {formatLocalDate(plan.updated_at)}
                        </TableCell>
                        <TableCell className="px-3 py-2.5">
                          <div className="flex items-center justify-end gap-0.5">
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() => navigate(`/execution/plan-execute?plan=${plan.id}`)}
                              title="执行"
                              aria-label={`执行 ${plan.name}`}
                            >
                              <Play className="w-4 h-4" />
                            </Button>
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() => navigate(`/orchestration/plans/${plan.id}`)}
                              title="编辑"
                              aria-label={`编辑 ${plan.name}`}
                            >
                              <Edit className="w-4 h-4" />
                            </Button>
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() => void handleDelete(plan)}
                              className={cn(TEXT.destructive, 'hover:text-destructive')}
                              title="删除"
                              aria-label={`删除 ${plan.name}`}
                            >
                              <Trash2 className="w-4 h-4" />
                            </Button>
                          </div>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            </section>
          ))}
        </div>
      )}
    </PageContainer>
  );
}
