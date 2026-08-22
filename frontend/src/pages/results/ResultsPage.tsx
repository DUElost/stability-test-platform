import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { Button } from '@/components/ui/button';
import { StatusBadge } from '@/components/ui/status-badge';
import { RiskDistributionChart } from '@/components/charts/RiskDistributionChart';
import { TestTypePassFailChart } from '@/components/charts/TestTypePassFailChart';
import { DashboardStatCard } from '@/components/dashboard/DashboardStatCard';
import { api, toApiError, type ResultsSummary } from '@/utils/api';
import {
  CheckCircle,
  XCircle,
  PlayCircle,
  ListChecks,
  Clock,
} from 'lucide-react';
import { PageContainer, PageHeader } from '@/components/layout';
import { formatDurationSeconds, formatLocalDateTime } from '@/utils/format';
import { InlineEmpty } from '@/components/ui/empty-state';
import { ErrorState } from '@/components/ui/error-state';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { ProjectFilterSelect, ProjectKeyBadge } from '@/components/project/ProjectFilterSelect';
import { useDocumentTitle } from '@/hooks/useDocumentTitle';
import { KPI_TONE, STAT } from '@/design-system/tokens';

export default function ResultsPage() {
  useDocumentTitle('测试结果');
  const navigate = useNavigate();
  // ADR-0029：页面级项目筛选（D5 快照语义——后端按 plan_run.project_id 过滤）
  const [projectKey, setProjectKey] = useState<string | undefined>(undefined);

  const { data, isLoading, isError, error, refetch } = useQuery<ResultsSummary>({
    queryKey: ['results-summary', { projectKey: projectKey ?? null }],
    queryFn: () => api.results.summary(30, projectKey),
    refetchInterval: 30_000,
  });

  const isProject404 = isError && toApiError(error).status === 404;
  const stats = data?.runs_by_status;

  if (isError) {
    return (
      <PageContainer width="content">
        <PageHeader title="测试结果" subtitle="测试运行统计与风险分布概览" />
        <ErrorState
          // 未知项目 key：按错误态渲染（后端统一 404），不吞成空数据
          title={isProject404 ? '项目不存在' : '加载测试结果失败'}
          description={isProject404
            ? `项目 "${projectKey}" 不存在，请清除筛选或核对 key`
            : toApiError(error).message}
          onRetry={isProject404 ? undefined : () => void refetch()}
          action={isProject404 ? (
            <Button variant="outline" onClick={() => setProjectKey(undefined)}>
              清除项目筛选
            </Button>
          ) : undefined}
        />
      </PageContainer>
    );
  }

  return (
    <PageContainer width="content">
      <PageHeader
        title="测试结果"
        subtitle="测试运行统计与风险分布概览"
        action={
          <ProjectFilterSelect
            value={projectKey}
            onChange={setProjectKey}
            className="w-52"
            testId="results-project-filter"
          />
        }
      />

      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <DashboardStatCard
          label="运行总数"
          value={stats?.total ?? 0}
          loading={isLoading}
          icon={<ListChecks size={18} className={KPI_TONE.default.label} />}
          iconWellClassName={STAT.iconWellMuted}
        />
        <DashboardStatCard
          label="已完成"
          value={stats?.finished ?? 0}
          loading={isLoading}
          icon={<CheckCircle size={18} className={KPI_TONE.success.value} />}
          iconWellClassName={STAT.iconWellSuccess}
          valueClassName={KPI_TONE.success.value}
        />
        <DashboardStatCard
          label="失败"
          value={stats?.failed ?? 0}
          loading={isLoading}
          icon={<XCircle size={18} className={KPI_TONE.destructive.value} />}
          iconWellClassName={STAT.iconWellDestructive}
          valueClassName={KPI_TONE.destructive.value}
        />
        <DashboardStatCard
          label="运行中"
          value={stats?.running ?? 0}
          loading={isLoading}
          icon={<PlayCircle size={18} className={KPI_TONE.primary.value} />}
          iconWellClassName={STAT.iconWellPrimary}
          valueClassName={KPI_TONE.primary.value}
        />
      </div>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <RiskDistributionChart
          data={data?.risk_distribution ?? { high: 0, medium: 0, low: 0, unknown: 0 }}
          isLoading={isLoading}
        />
        <TestTypePassFailChart
          data={data?.test_type_stats ?? []}
          isLoading={isLoading}
        />
      </div>

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="flex items-center gap-2 text-sm font-medium">
            <Clock size={16} className="text-muted-foreground" />
            最近运行
          </CardTitle>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <div className="space-y-2">
              {Array.from({ length: 5 }).map((_, i) => (
                <Skeleton key={i} className="h-10 w-full" />
              ))}
            </div>
          ) : !data?.recent_runs?.length ? (
            <InlineEmpty>暂无测试运行 · 还没有执行过测试</InlineEmpty>
          ) : (
            <div className="overflow-x-auto">
              <Table className="min-w-[640px]">
                <TableHeader>
                  <TableRow className="border-b text-left text-xs text-muted-foreground">
                    <TableHead className="pb-2 pr-4">Run</TableHead>
                    <TableHead className="pb-2 pr-4">任务</TableHead>
                    <TableHead className="pb-2 pr-4">状态</TableHead>
                    <TableHead className="pb-2 pr-4">风险</TableHead>
                    <TableHead className="pb-2 pr-4">时长</TableHead>
                    <TableHead className="pb-2">开始时间</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data.recent_runs.map((run) => (
                    <TableRow
                      key={run.run_id}
                      className="cursor-pointer border-b transition-colors last:border-0 hover:bg-muted/50"
                      onClick={() => navigate(`/runs/${run.run_id}/report`)}
                    >
                      <TableCell className="py-2 pr-4 font-mono text-xs">#{run.run_id}</TableCell>
                      <TableCell className="max-w-[180px] truncate py-2 pr-4">
                        <span className="flex items-center gap-1.5">
                          <span className="truncate">{run.task_name}</span>
                          <ProjectKeyBadge projectKey={run.project_key} />
                        </span>
                      </TableCell>
                      <TableCell className="py-2 pr-4">
                        <StatusBadge kind="job-result" status={run.status} size="sm" fallbackToRaw />
                      </TableCell>
                      <TableCell className="py-2 pr-4">
                        <StatusBadge kind="risk" status={run.risk_level} size="sm" />
                      </TableCell>
                      <TableCell className="py-2 pr-4 text-xs text-muted-foreground">
                        {formatDurationSeconds(run.duration_seconds, 'precise', '-')}
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
    </PageContainer>
  );
}
