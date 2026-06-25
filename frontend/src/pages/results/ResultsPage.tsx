import { useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { StatusBadge } from '@/components/ui/status-badge';
import { RiskDistributionChart } from '@/components/charts/RiskDistributionChart';
import { TestTypePassFailChart } from '@/components/charts/TestTypePassFailChart';
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
import { KPI_TONE, RUN_RESULT_STATUS_CHIP, STATUS_CHIP } from '@/design-system/tokens';
import { cn } from '@/lib/utils';

export default function ResultsPage() {
  const navigate = useNavigate();

  const { data, isLoading } = useQuery<ResultsSummary>({
    queryKey: ['results-summary'],
    queryFn: async () => {
      const resp = await api.results.summary(30);
      return resp.data;
    },
    refetchInterval: 30_000,
  });

  const stats = data?.runs_by_status;

  return (
    <PageContainer width="default">
      <PageHeader title="测试结果" subtitle="测试运行统计与风险分布概览" />

      {/* Stat cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard
          label="Total Runs"
          value={stats?.total}
          icon={<ListChecks size={18} className={KPI_TONE.default.label} />}
          isLoading={isLoading}
        />
        <StatCard
          label="Finished"
          value={stats?.finished}
          icon={<CheckCircle size={18} className={KPI_TONE.success.value} />}
          isLoading={isLoading}
        />
        <StatCard
          label="Failed"
          value={stats?.failed}
          icon={<XCircle size={18} className={KPI_TONE.destructive.value} />}
          isLoading={isLoading}
        />
        <StatCard
          label="Running"
          value={stats?.running}
          icon={<PlayCircle size={18} className={KPI_TONE.primary.value} />}
          isLoading={isLoading}
        />
      </div>

      {/* Charts row */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <RiskDistributionChart
          data={data?.risk_distribution ?? { high: 0, medium: 0, low: 0, unknown: 0 }}
          isLoading={isLoading}
        />
        <TestTypePassFailChart
          data={data?.test_type_stats ?? []}
          isLoading={isLoading}
        />
      </div>

      {/* Recent runs table */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-sm font-medium flex items-center gap-2">
            <Clock size={16} className="text-muted-foreground" />
            Recent Runs
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
                icon={<Clock className="w-12 h-12" />}
              />
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b text-left text-xs text-muted-foreground">
                    <th className="pb-2 pr-4">Run</th>
                    <th className="pb-2 pr-4">Task</th>
                    <th className="pb-2 pr-4">Type</th>
                    <th className="pb-2 pr-4">Status</th>
                    <th className="pb-2 pr-4">Risk</th>
                    <th className="pb-2 pr-4">Duration</th>
                    <th className="pb-2">Started</th>
                  </tr>
                </thead>
                <tbody>
                  {data.recent_runs.map((run) => (
                    <tr
                      key={run.run_id}
                      className="border-b last:border-0 hover:bg-muted/50 cursor-pointer transition-colors"
                      onClick={() => navigate(`/runs/${run.run_id}/report`)}
                    >
                      <td className="py-2 pr-4 font-mono text-xs">#{run.run_id}</td>
                      <td className="py-2 pr-4 truncate max-w-[180px]">{run.task_name}</td>
                      <td className="py-2 pr-4">
                        <span className={cn('px-1.5 py-0.5 rounded text-xs font-medium', STATUS_CHIP.muted)}>
                          {run.task_type}
                        </span>
                      </td>
                      <td className="py-2 pr-4">
                        <span className={cn('px-1.5 py-0.5 rounded text-xs font-medium', RUN_RESULT_STATUS_CHIP[run.status] ?? STATUS_CHIP.muted)}>
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

function StatCard({
  label,
  value,
  icon,
  isLoading,
}: {
  label: string;
  value?: number;
  icon: React.ReactNode;
  isLoading?: boolean;
}) {
  return (
    <Card>
      <CardContent className="pt-4 pb-3 px-4">
        <div className="flex items-center justify-between mb-1">
          <span className="text-xs text-muted-foreground">{label}</span>
          {icon}
        </div>
        {isLoading ? (
          <Skeleton className="h-7 w-16" />
        ) : (
          <span className="text-2xl font-semibold">{value ?? 0}</span>
        )}
      </CardContent>
    </Card>
  );
}
