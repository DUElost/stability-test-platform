import { useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { StatusBadge } from '@/components/ui/status-badge';
import { RiskDistributionChart } from '@/components/charts/RiskDistributionChart';
import { TestTypePassFailChart } from '@/components/charts/TestTypePassFailChart';
import { DashboardStatCard } from '@/components/dashboard/DashboardStatCard';
import { api, type ResultsSummary } from '@/utils/api';
import {
  CheckCircle,
  XCircle,
  PlayCircle,
  ListChecks,
  Clock,
} from 'lucide-react';
import { PageContainer, PageHeader } from '@/components/layout';
import { formatDurationSeconds, formatLocalDateTime } from '@/utils/format';
import { EmptyState } from '@/components/ui/empty-state';
import { useDocumentTitle } from '@/hooks/useDocumentTitle';
import { KPI_TONE, RUN_RESULT_STATUS_CHIP, STAT, STATUS_CHIP } from '@/design-system/tokens';
import { cn } from '@/lib/utils';

export default function ResultsPage() {
  useDocumentTitle('测试结果');
  const navigate = useNavigate();

  const { data, isLoading } = useQuery<ResultsSummary>({
    queryKey: ['results-summary'],
    queryFn: () => api.results.summary(30),
    refetchInterval: 30_000,
  });

  const stats = data?.runs_by_status;

  return (
    <PageContainer width="default">
      <PageHeader title="测试结果" subtitle="测试运行统计与风险分布概览" />

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
            <div className="py-8">
              <EmptyState
                title="暂无测试运行"
                description="还没有执行过测试"
                icon={<Clock className="h-12 w-12" />}
              />
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b text-left text-xs text-muted-foreground">
                    <th className="pb-2 pr-4">Run</th>
                    <th className="pb-2 pr-4">任务</th>
                    <th className="pb-2 pr-4">类型</th>
                    <th className="pb-2 pr-4">状态</th>
                    <th className="pb-2 pr-4">风险</th>
                    <th className="pb-2 pr-4">时长</th>
                    <th className="pb-2">开始时间</th>
                  </tr>
                </thead>
                <tbody>
                  {data.recent_runs.map((run) => (
                    <tr
                      key={run.run_id}
                      className="cursor-pointer border-b transition-colors last:border-0 hover:bg-muted/50"
                      onClick={() => navigate(`/runs/${run.run_id}/report`)}
                    >
                      <td className="py-2 pr-4 font-mono text-xs">#{run.run_id}</td>
                      <td className="max-w-[180px] truncate py-2 pr-4">{run.task_name}</td>
                      <td className="py-2 pr-4">
                        <span className={cn('rounded px-1.5 py-0.5 text-xs font-medium', STATUS_CHIP.muted)}>
                          {run.task_type}
                        </span>
                      </td>
                      <td className="py-2 pr-4">
                        <span className={cn('rounded px-1.5 py-0.5 text-xs font-medium', RUN_RESULT_STATUS_CHIP[run.status] ?? STATUS_CHIP.muted)}>
                          {run.status}
                        </span>
                      </td>
                      <td className="py-2 pr-4">
                        <StatusBadge kind="risk" status={run.risk_level} size="sm" />
                      </td>
                      <td className="py-2 pr-4 text-xs text-muted-foreground">
                        {formatDurationSeconds(run.duration_seconds, 'precise', '-')}
                      </td>
                      <td className="py-2 text-xs text-muted-foreground">
                        {formatLocalDateTime(run.started_at)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>
    </PageContainer>
  );
}
