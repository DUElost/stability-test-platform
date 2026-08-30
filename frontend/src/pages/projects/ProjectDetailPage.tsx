import { useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import {
  Archive,
  ArrowLeft,
  Pencil,
  Smartphone,
  FileBox,
  ListTodo,
  Link2,
  TicketCheck,
  X,
} from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { StatusBadge } from '@/components/ui/status-badge';
import { PageContainer, PageHeader } from '@/components/layout';
import { ErrorState } from '@/components/ui/error-state';
import { InlineEmpty } from '@/components/ui/empty-state';
import { Skeleton } from '@/components/ui/skeleton';
import { TEXT } from '@/design-system/tokens';
import { cn } from '@/lib/utils';
import { useDocumentTitle } from '@/hooks/useDocumentTitle';
import { useAuthSession } from '@/hooks/useAuthSession';
import { useToast } from '@/hooks/useToast';
import { api, toApiError } from '@/utils/api';
import { projectKeys } from '@/utils/api/queryKeys';
import type { ProjectUpdateInput } from '@/utils/api/types';
import EditProjectDialog from './components/EditProjectDialog';
import { coverageSummary } from './inventoryDisplay';
import { FACET_FIELD_ENTRIES } from './facetFields';

export default function ProjectDetailPage() {
  const { projectKey = '' } = useParams<{ projectKey: string }>();
  const navigate = useNavigate();
  useDocumentTitle(`项目 ${projectKey}`);

  const detailQ = useQuery({
    queryKey: projectKeys.detail(projectKey),
    queryFn: () => api.projects.get(projectKey),
  });

  const devicesQ = useQuery({
    queryKey: projectKeys.devicesOf(projectKey),
    queryFn: () => api.devices.list(0, 20, undefined, undefined, projectKey),
  });

  const plansQ = useQuery({
    queryKey: projectKeys.plansOf(projectKey),
    queryFn: () => api.plans.list(0, 20, projectKey),
  });

  // ADR-0029 P2：项目级风险趋势（按天 S/A/B，run 级 DLE 权威聚合）
  const riskTrendQ = useQuery({
    queryKey: ['project-risk-trend', projectKey],
    queryFn: () => api.results.riskTrend(projectKey, 30),
  });

  const modelsQ = useQuery({
    queryKey: projectKeys.modelsOf(projectKey),
    queryFn: () => api.projects.modelsOf(projectKey),
  });

  // G17 收尾：登记簿编辑入口（后端 PUT 仅 admin；fields_set 语义，显式 null 即清空）
  const sessionQ = useAuthSession();
  const isAdmin = sessionQ.data?.role === 'admin';
  const toast = useToast();
  const queryClient = useQueryClient();
  const [editOpen, setEditOpen] = useState(false);
  const updateMutation = useMutation({
    mutationFn: (payload: ProjectUpdateInput) => api.projects.update(projectKey, payload),
    onSuccess: () => {
      toast.success('项目信息已更新');
      setEditOpen(false);
      void queryClient.invalidateQueries({ queryKey: projectKeys.detail(projectKey) });
      void queryClient.invalidateQueries({ queryKey: projectKeys.list() });
    },
    onError: (error) => {
      toast.error(`更新失败: ${toApiError(error).message || '请稍后重试'}`);
    },
  });

  // ADR-0029 复盘：移除型号规则（admin）。已归属设备不动，心跳按新状态收敛。
  const removeRuleMutation = useMutation({
    mutationFn: ({ model }: { model: string }) =>
      api.projects.removeRule(projectKey, model),
    onSuccess: () => {
      toast.success('规则已移除');
      void queryClient.invalidateQueries({ queryKey: projectKeys.detail(projectKey) });
      void queryClient.invalidateQueries({ queryKey: projectKeys.inventoryModels() });
      void queryClient.invalidateQueries({ queryKey: projectKeys.inventorySummary() });
    },
    onError: (error) => {
      toast.error(`移除失败: ${toApiError(error).message || '请稍后重试'}`);
    },
  });

  // D2 复核：项目重命名——改 key 后跳新 URL（外键不受影响）
  const renameMutation = useMutation({
    mutationFn: (newKey: string) => api.projects.rename(projectKey, newKey),
    onSuccess: (renamed) => {
      toast.success(`已重命名为 ${renamed.project_key}`);
      setEditOpen(false);
      void queryClient.invalidateQueries({ queryKey: projectKeys.list() });
      navigate(`/projects/${renamed.project_key}`);
    },
    onError: (error) => {
      toast.error(`重命名失败: ${toApiError(error).message || '请稍后重试'}`);
    },
  });

  const handleRemoveRule = (model: string) => {
    if (!window.confirm(`移除型号 ${model} 的归属规则？设备归属不动，心跳按新规则状态收敛。`)) {
      return;
    }
    removeRuleMutation.mutate({ model });
  };

  // 归档：登记簿的终结操作（无删除设计）。归档后从活跃列表消失，历史数据保留。
  const archiveMutation = useMutation({
    mutationFn: () => api.projects.archive(projectKey),
    onSuccess: () => {
      toast.success('项目已归档');
      void queryClient.invalidateQueries({ queryKey: projectKeys.detail(projectKey) });
      void queryClient.invalidateQueries({ queryKey: projectKeys.list() });
    },
    onError: (error) => {
      toast.error(`归档失败: ${toApiError(error).message || '请稍后重试'}`);
    },
  });

  // 按规则重算存量归属（显式纠正——心跳不覆盖已归属设备）
  const recomputeMutation = useMutation({
    mutationFn: () => api.projects.recomputeRules(projectKey),
    onSuccess: (data) => {
      toast.success(`已按规则重算：${data.devices_moved} 台设备归位`);
      void queryClient.invalidateQueries({ queryKey: projectKeys.detail(projectKey) });
      void queryClient.invalidateQueries({ queryKey: projectKeys.inventoryModels() });
      void queryClient.invalidateQueries({ queryKey: projectKeys.inventorySummary() });
    },
    onError: (error) => {
      toast.error(`重算失败: ${toApiError(error).message || '请稍后重试'}`);
    },
  });

  const handleRecompute = () => {
    if (!window.confirm('按本项目的归属规则重算存量设备？未归属或归属错误的设备将归入本项目（钉住设备跳过）。')) {
      return;
    }
    recomputeMutation.mutate();
  };

  const handleArchive = () => {
    if (!window.confirm('归档此项目？归档后从活跃列表消失（历史 Run 与设备归属保留），可在列表用状态筛选查看。')) {
      return;
    }
    archiveMutation.mutate();
  };

  if (detailQ.isLoading) {
    return (
      <PageContainer width="content">
        <PageHeader title="项目详情" subtitle={projectKey} />
        <div className="space-y-4">
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-48 w-full" />
        </div>
      </PageContainer>
    );
  }

  if (detailQ.isError) {
    const status = toApiError(detailQ.error).status;
    const isNotFound = status === 404;
    return (
      <PageContainer width="content">
        <PageHeader title="项目详情" subtitle={projectKey} />
        <ErrorState
          // 约束 2：未知 key 是路由错误（后端统一 404），按错误态渲染不吞成空态
          title={isNotFound ? '项目不存在' : '加载项目失败'}
          description={
            isNotFound
              ? `项目 "${projectKey}" 不存在，请检查链接或返回列表`
              : (detailQ.error as Error)?.message || '请检查网络连接或稍后重试'
          }
          action={
            <Button variant="outline" onClick={() => navigate('/projects')}>
              <ArrowLeft className="h-4 w-4 mr-2" />
              返回项目列表
            </Button>
          }
          onRetry={isNotFound ? undefined : () => void detailQ.refetch()}
        />
      </PageContainer>
    );
  }

  // isLoading / isError 已 return；此处必然有值（TS 无法收窄 useQuery.data）
  const project = detailQ.data!;
  const devices = devicesQ.data?.items ?? [];
  const plans = plansQ.data ?? [];

  return (
    <PageContainer width="content">
      <PageHeader
        title={project.display_name}
        subtitle={project.project_key}
        breadcrumbs={[{ label: '项目登记簿', path: '/projects' }]}
      />

      {/* 头部：facet + jira + status */}
      <Card>
        <CardContent className="py-4">
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant={project.status === 'ACTIVE' ? 'success' : 'secondary'}>
              {project.status === 'ACTIVE' ? '启用' : '归档'}
            </Badge>
            {FACET_FIELD_ENTRIES.map(([field, label]) => {
              const value = project[field];
              if (Array.isArray(value)) {
                if (value.length === 0) return null;
                return (
                  <Badge key={field} variant="outline">
                    {label}: {value.join('、')}
                  </Badge>
                );
              }
              if (!value) return null;
              return (
                <Badge key={field} variant="outline">
                  {label}: {value}
                </Badge>
              );
            })}
            {project.jira_project_key ? (
              <Badge variant="info">
                <TicketCheck className="h-3 w-3 mr-1" />
                JIRA: {project.jira_project_key}
              </Badge>
            ) : (
              <Badge variant="outline" data-testid="jira-not-configured">
                JIRA: 未配置
              </Badge>
            )}
            {project.source === 'SEED' ? (
              <Badge variant="secondary" className="text-[11px] font-normal">
                系统回填（不在工作台列出）
              </Badge>
            ) : null}
            {isAdmin ? (
              <Button
                variant="outline"
                size="sm"
                className="ml-auto"
                data-testid="edit-project-open"
                onClick={() => setEditOpen(true)}
                disabled={project.status === 'ARCHIVED'}
              >
                <Pencil className="h-3.5 w-3.5 mr-1" />
                编辑
              </Button>
            ) : null}
            {isAdmin && project.status === 'ACTIVE' ? (
              <Button
                variant="ghost"
                size="sm"
                data-testid="archive-project-open"
                onClick={() => handleArchive()}
              >
                <Archive size={14} className="mr-1.5" />
                归档
              </Button>
            ) : null}
          </div>
          {project.source === 'SEED' ? (
            <p className={cn('mt-3 text-xs', TEXT.subtitle)} data-testid="seed-disclaimer">
              这是 P1 脚本灌入的回填标签，不能代表客户、项目或机型。请在工作台新建人工项目并映射型号。
            </p>
          ) : null}
        </CardContent>
      </Card>

      {/* 归属规则（规则就是项目的定义本身——提为主块） */}
      <Card data-testid="detail-rules">
        <CardHeader className="flex items-center justify-between gap-2 pb-3">
          <CardTitle className="flex items-center gap-2 text-sm font-medium">
            <Link2 size={16} className="text-muted-foreground" />
            归属规则
          </CardTitle>
          {isAdmin ? (
            <Button
              variant="outline"
              size="sm"
              data-testid="recompute-rules-open"
              onClick={() => handleRecompute()}
            >
              按规则重算
            </Button>
          ) : null}
        </CardHeader>
        <CardContent className="py-3">
          {(project.match_models ?? []).length > 0 ? (
            <div className="flex flex-wrap items-center gap-1.5">
              {(project.match_models ?? []).map((model) => (
                <span key={model} className="inline-flex items-center gap-1">
                  <Badge variant="outline" className="font-mono text-xs">
                    {model}
                  </Badge>
                  {isAdmin && (
                    <button
                      type="button"
                      aria-label={`移除规则 ${model}`}
                      title="删除此型号规则（设备归属不动，心跳按新规则状态收敛）"
                      className="text-muted-foreground hover:text-destructive"
                      onClick={() => handleRemoveRule(model)}
                    >
                      <X size={12} />
                    </button>
                  )}
                </span>
              ))}
            </div>
          ) : (
            <p className={cn('text-xs', TEXT.subtitle)} data-testid="rules-empty">
              暂无归属规则
            </p>
          )}
          {modelsQ.isLoading ? (
            <Skeleton className="mt-2 h-5 w-40" />
          ) : modelsQ.data && modelsQ.data.length > 0 ? (
            <p className={cn('mt-2 text-xs', TEXT.subtitle)} data-testid="hanging-models">
              当前归属此项目的设备型号：{coverageSummary(modelsQ.data)}
            </p>
          ) : (
            <p className={cn('mt-2 text-xs', TEXT.subtitle)} data-testid="hanging-models-empty">
              当前没有设备归属此项目
            </p>
          )}
          <Button
            variant="link"
            size="sm"
            className="mt-1 h-auto p-0 text-xs"
            onClick={() => navigate('/projects')}
          >
            在项目登记簿维护归属规则（型号 → 项目映射）
          </Button>
        </CardContent>
      </Card>

      {/* KPI 带（与列表页/抽屉同款形态） */}
      <Card data-testid="detail-kpi-strip">
        <CardContent className="py-3">
          <div className="grid grid-cols-2 divide-x md:grid-cols-4">
            <div className="px-4 py-1 text-center">
              <p className="text-lg font-bold leading-none text-foreground">{project.device_count}</p>
              <p className={cn('mt-1 text-[11px]', TEXT.subtitle)}>设备</p>
            </div>
            <div className="px-4 py-1 text-center">
              <p className="text-lg font-bold leading-none text-foreground">{project.plan_count}</p>
              <p className={cn('mt-1 text-[11px]', TEXT.subtitle)}>Plan</p>
            </div>
            <div className="px-4 py-1 text-center">
              <p className="text-lg font-bold leading-none text-foreground">{project.total_run_count}</p>
              <p className={cn('mt-1 text-[11px]', TEXT.subtitle)}>历史 Run</p>
            </div>
            <div className="px-4 py-1 text-center">
              <p
                className={cn(
                  'text-lg font-bold leading-none',
                  project.running_run_count > 0 ? 'text-success' : 'text-foreground',
                )}
              >
                {project.running_run_count}
              </p>
              <p className={cn('mt-1 text-[11px]', TEXT.subtitle)}>在跑 Run</p>
            </div>
          </div>
        </CardContent>
      </Card>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {/* 设备块 */}
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="flex items-center gap-2 text-sm font-medium">
              <Smartphone size={16} className="text-muted-foreground" />
              设备（{project.device_count}）
            </CardTitle>
          </CardHeader>
          <CardContent className="py-3">
            {devicesQ.isLoading ? (
              <Skeleton className="h-24 w-full" />
            ) : devices.length === 0 ? (
              <InlineEmpty>该项目暂无设备</InlineEmpty>
            ) : (
              <ul className="divide-y">
                {devices.map((device) => (
                  <li key={device.id} className="flex items-center justify-between py-2">
                    <span className="font-mono text-xs">{device.serial}</span>
                    <div className="flex items-center gap-2">
                      <span className={cn('text-xs', TEXT.subtitle)}>
                        {device.model || '未知设备'}
                      </span>
                      <StatusBadge kind="device" status={device.status} size="sm" />
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>

        {/* 计划块 */}
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="flex items-center gap-2 text-sm font-medium">
              <FileBox size={16} className="text-muted-foreground" />
              计划（{project.plan_count}）
            </CardTitle>
          </CardHeader>
          <CardContent className="py-3">
            {plansQ.isLoading ? (
              <Skeleton className="h-24 w-full" />
            ) : plans.length === 0 ? (
              <InlineEmpty>该项目暂无计划</InlineEmpty>
            ) : (
              <ul className="divide-y">
                {plans.map((plan) => (
                  <li key={plan.id} className="flex items-center justify-between py-2">
                    <span className="truncate text-sm">{plan.name}</span>
                    <span className={cn('shrink-0 text-xs', TEXT.subtitle)}>
                      {plan.steps?.length ?? 0} 步骤
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>

        {/* 结果块：风险趋势（按天 S/A/B）——最近运行列表在 /results 页已有 */}
        <Card className="lg:col-span-2">
          <CardHeader className="pb-3">
            <CardTitle className="flex items-center gap-2 text-sm font-medium">
              <ListTodo size={16} className="text-muted-foreground" />
              风险趋势（近 30 天 · S/A/B）
            </CardTitle>
          </CardHeader>
          <CardContent>
            {riskTrendQ.isLoading ? (
              <Skeleton className="h-56 w-full" />
            ) : !riskTrendQ.data?.buckets?.length ? (
              <InlineEmpty>
                暂无风险数据——新 Run 派发完成后开始积累（S/A/B 由事件定级）
              </InlineEmpty>
            ) : (
              <div className="h-56 w-full">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart
                    data={riskTrendQ.data.buckets}
                    margin={{ top: 4, right: 8, bottom: 0, left: -8 }}
                    barCategoryGap="20%"
                  >
                    <CartesianGrid
                      strokeDasharray="3 3"
                      vertical={false}
                      stroke="hsl(var(--border))"
                    />
                    <XAxis
                      dataKey="date"
                      tick={{ fontSize: 11, fill: 'hsl(var(--muted-foreground))' }}
                      tickLine={false}
                      axisLine={false}
                    />
                    <YAxis
                      allowDecimals={false}
                      width={28}
                      tick={{ fontSize: 11, fill: 'hsl(var(--muted-foreground))' }}
                      tickLine={false}
                      axisLine={false}
                    />
                    <Tooltip
                      cursor={{ fill: 'hsl(var(--muted) / 0.35)' }}
                      contentStyle={{
                        fontSize: 12,
                        borderRadius: 8,
                        border: '1px solid hsl(var(--border))',
                        background: 'hsl(var(--card))',
                      }}
                    />
                    <Legend wrapperStyle={{ fontSize: 12 }} />
                    {/* 状态语义色：S=critical / A=warning / B=good（dataviz 规范） */}
                    <Bar dataKey="S" name="S（致命）" stackId="risk"
                         fill="hsl(var(--destructive))" />
                    <Bar dataKey="A" name="A（高）" stackId="risk"
                         fill="hsl(var(--warning))" />
                    <Bar dataKey="B" name="B（低）" stackId="risk"
                         fill="hsl(var(--success))" radius={[3, 3, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      {/* 编辑项目（G17 收尾：JIRA 键等登记簿字段的人工修正通道） */}
      {isAdmin && project ? (
        <EditProjectDialog
          isOpen={editOpen}
          isSubmitting={updateMutation.isPending}
          project={project}
          onClose={() => setEditOpen(false)}
          onSubmit={(payload) => updateMutation.mutate(payload)}
          onRename={(newKey) => renameMutation.mutate(newKey)}
        />
      ) : null}
    </PageContainer>
  );
}
