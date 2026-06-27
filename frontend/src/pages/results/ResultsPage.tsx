import { useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
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
import { PageContainer, PageHeaderV2 } from '@/components/layout';
import { DataTable, DataEmptyState } from '@/components/data';
import { formatDurationSeconds, formatLocalDateTime } from '@/utils/format';
import { KPI_TONE, RUN_RESULT_STATUS_CHIP, STAT, STATUS_CHIP } from '@/design-system/tokens';
import { cn } from '@/lib/utils';
import type { ColumnDef } from '@tanstack/react-table';

type RecentRun = NonNullable<ResultsSummary['recent_runs']>[number];

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

  const columns: ColumnDef<RecentRun>[] = [
    {
      accessorKey: 'run_id',
      header: 'Run',
      cell: ({ getValue }) => <span className="font-mono text-xs">#{getValue<number>()}</span>,
      size: 70,
    },
    {
      accessorKey: 'task_name',
      header: '任务',
      cell: ({ getValue }) => (
        <span className="max-w-[180px] truncate block">{getValue<string>()}</span>
      ),
      size: 200,
    },
    {
      accessorKey: 'task_type',
      header: '类型',
      cell: ({ getValue }) => (
        <span className={cn('rounded px-1.5 py-0.5 text-xs font-medium', STATUS_CHIP.muted)}>
          {getValue<string>()}
        </span>
      ),
      size: 100,
    },
    {
      accessorKey: 'status',
      header: '状态',
      cell: ({ getValue }) => (
        <span
          className={cn(
            'rounded px-1.5 py-0.5 text-xs font-medium',
            RUN_RESULT_STATUS_CHIP[getValue<string>()] ?? STATUS_CHIP.muted,
          )}
        >
          {getValue<string>()}
        </span>
      ),
      size: 100,
    },
    {
      accessorKey: 'risk_level',
      header: '风险',
      cell: ({ getValue }) => <StatusBadge kind="risk" status={getValue<string>()} size="sm" />,
      size: 80,
    },
    {
      accessorKey: 'duration_seconds',
      header: '时长',
      cell: ({ getValue }) => (
        <span className="text-xs text-muted-foreground">
          {formatDurationSeconds(getValue<number | null>(), 'precise', '-')}
        </span>
      ),
      size: 100,
    },
    {
      accessorKey: 'started_at',
      header: '开始时间',
      cell: ({ getValue }) => (
        <span className="text-xs text-muted-foreground">
          {formatLocalDateTime(getValue<string | null>())}
        </span>
      ),
      size: 150,
    },
  ];

  return (
    <PageContainer fullBleed>
      <PageHeaderV2 title="测试结果" description="测试运行统计与风险分布概览" />

      <div className="grid grid-cols-2 gap-4 md:grid-cols-4 px-6">
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

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 px-6">
        <RiskDistributionChart
          data={data?.risk_distribution ?? { high: 0, medium: 0, low: 0, unknown: 0 }}
          isLoading={isLoading}
        />
        <TestTypePassFailChart
          data={data?.test_type_stats ?? []}
          isLoading={isLoading}
        />
      </div>

      <Card className="mx-6 mb-6">
        <CardHeader className="pb-3">
          <CardTitle className="flex items-center gap-2 text-sm font-medium">
            <Clock size={16} className="text-muted-foreground" />
            最近运行
          </CardTitle>
        </CardHeader>
        <CardContent>
          <DataTable
            data={data?.recent_runs ?? []}
            columns={columns}
            isLoading={isLoading}
            getRowId={(row) => String(row.run_id)}
            rowActions={(row) => [
              {
                label: '查看报告',
                onClick: () => navigate(`/runs/${row.run_id}/report`),
              },
            ]}
            emptyState={
              <DataEmptyState
                title="暂无测试运行"
                description="还没有执行过测试"
                icon={<Clock className="h-12 w-12" />}
              />
            }
          />
        </CardContent>
      </Card>
    </PageContainer>
  );
}
