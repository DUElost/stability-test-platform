import { useNavigate, useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import {
  ArrowLeft,
  Smartphone,
  FileBox,
  ListTodo,
  TicketCheck,
} from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { StatusBadge } from '@/components/ui/status-badge';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { PageContainer, PageHeader } from '@/components/layout';
import { ErrorState } from '@/components/ui/error-state';
import { InlineEmpty } from '@/components/ui/empty-state';
import { Skeleton } from '@/components/ui/skeleton';
import { STAT, TEXT } from '@/design-system/tokens';
import { cn } from '@/lib/utils';
import { useDocumentTitle } from '@/hooks/useDocumentTitle';
import { api, toApiError } from '@/utils/api';
import { projectKeys } from '@/utils/api/queryKeys';
import { formatLocalDateTime } from '@/utils/format';
import { coverageSummary } from './inventoryDisplay';

const FACET_FIELDS = [
  ['customer', '客户'],
  ['platform', '平台'],
  ['form_factor', '形态'],
  ['product_line', '产品线'],
] as const;

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

  const summaryQ = useQuery({
    queryKey: projectKeys.summaryOf(projectKey),
    queryFn: () => api.results.summary(5, projectKey),
  });

  const modelsQ = useQuery({
    queryKey: projectKeys.modelsOf(projectKey),
    queryFn: () => api.projects.modelsOf(projectKey),
  });

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
  const summary = summaryQ.data;

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
            {FACET_FIELDS.map(([field, label]) => {
              const value = project[field];
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
          </div>
          {project.source === 'SEED' ? (
            <p className={cn('mt-3 text-xs', TEXT.subtitle)} data-testid="seed-disclaimer">
              这是 P1 脚本灌入的回填标签，不能代表客户、项目或机型。请在工作台新建人工项目并映射型号。
            </p>
          ) : (project.match_models ?? []).length > 0 ? (
            <p className={cn('mt-3 font-mono text-xs', TEXT.subtitle)} data-testid="match-models">
              已映射型号：{(project.match_models ?? []).join(' · ')}
            </p>
          ) : (
            <p className={cn('mt-3 text-xs', TEXT.subtitle)} data-testid="match-models-empty">
              尚未映射型号。请在工作台勾选型号后填写。
            </p>
          )}
          {modelsQ.isLoading ? (
            <Skeleton className="mt-2 h-5 w-64" />
          ) : modelsQ.data && modelsQ.data.length > 0 ? (
            <p className={cn('mt-2 font-mono text-xs', TEXT.subtitle)} data-testid="hanging-models">
              当前归属此项目的设备型号：{coverageSummary(modelsQ.data)}
            </p>
          ) : (
            <p className={cn('mt-2 text-xs', TEXT.subtitle)} data-testid="hanging-models-empty">
              当前没有设备归属此项目
            </p>
          )}
        </CardContent>
      </Card>

      {/* 统计 */}
      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <Card>
          <CardContent className="py-4 text-center">
            <p className={STAT.value}>{project.device_count}</p>
            <p className={STAT.label}>设备</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="py-4 text-center">
            <p className={STAT.value}>{project.plan_count}</p>
            <p className={STAT.label}>Plan</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="py-4 text-center">
            <p className={STAT.value}>{project.total_run_count}</p>
            <p className={STAT.label}>历史 Run</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="py-4 text-center">
            <p className={STAT.value}>{project.running_run_count}</p>
            <p className={STAT.label}>在跑 Run</p>
          </CardContent>
        </Card>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {/* 设备块 */}
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="flex items-center gap-2 text-sm font-medium">
              <Smartphone size={16} className="text-muted-foreground" />
              设备（{project.device_count}）
            </CardTitle>
          </CardHeader>
          <CardContent>
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
          <CardContent>
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

        {/* 结果块 */}
        <Card className="lg:col-span-2">
          <CardHeader className="pb-3">
            <CardTitle className="flex items-center gap-2 text-sm font-medium">
              <ListTodo size={16} className="text-muted-foreground" />
              最近运行（快照语义：按 plan_run.project_id 归属）
            </CardTitle>
          </CardHeader>
          <CardContent>
            {summaryQ.isLoading ? (
              <Skeleton className="h-32 w-full" />
            ) : !summary?.recent_runs?.length ? (
              <InlineEmpty>该项目暂无运行记录</InlineEmpty>
            ) : (
              <div className="overflow-x-auto">
                <Table className="min-w-[560px]">
                  <TableHeader>
                    <TableRow className="border-b text-left text-xs text-muted-foreground">
                      <TableHead className="pb-2 pr-4">Run</TableHead>
                      <TableHead className="pb-2 pr-4">任务</TableHead>
                      <TableHead className="pb-2 pr-4">状态</TableHead>
                      <TableHead className="pb-2 pr-4">风险</TableHead>
                      <TableHead className="pb-2">开始时间</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {summary.recent_runs.map((run) => (
                      <TableRow
                        key={run.run_id}
                        className="cursor-pointer border-b transition-colors last:border-0 hover:bg-muted/50"
                        onClick={() => navigate(`/runs/${run.run_id}/report`)}
                      >
                        <TableCell className="py-2 pr-4 font-mono text-xs">#{run.run_id}</TableCell>
                        <TableCell className="max-w-[240px] truncate py-2 pr-4">{run.task_name}</TableCell>
                        <TableCell className="py-2 pr-4">
                          <StatusBadge kind="job-result" status={run.status} size="sm" fallbackToRaw />
                        </TableCell>
                        <TableCell className="py-2 pr-4">
                          <StatusBadge kind="risk" status={run.risk_level} size="sm" />
                        </TableCell>
                        <TableCell className="py-2 text-xs text-muted-foreground">
                          {formatLocalDateTime(run.started_at)}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}
          </CardContent>
        </Card>

        {/* JIRA 块 */}
        <Card className="lg:col-span-2">
          <CardHeader className="pb-3">
            <CardTitle className="flex items-center gap-2 text-sm font-medium">
              <TicketCheck size={16} className="text-muted-foreground" />
              JIRA 集成
            </CardTitle>
          </CardHeader>
          <CardContent className={cn('text-sm', TEXT.subtitle)}>
            {project.jira_project_key ? (
              <p>
                提交 JIRA 时将自动带出项目关键字{' '}
                <span className="font-mono text-foreground">{project.jira_project_key}</span>（P3
                落地后生效）。
              </p>
            ) : (
              <p data-testid="jira-placeholder">
                尚未配置 JIRA 项目关键字。P3 落地后由管理员在此维护。
              </p>
            )}
          </CardContent>
        </Card>
      </div>
    </PageContainer>
  );
}
