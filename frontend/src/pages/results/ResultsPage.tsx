import { useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { RiskDistributionChart } from '@/components/charts/RiskDistributionChart';
import { TestTypePassFailChart } from '@/components/charts/TestTypePassFailChart';
import { api, type ResultsSummary } from '@/utils/api';
import {
  CheckCircle,
  XCircle,
  PlayCircle,
  ListChecks,
  Clock,
  ShieldAlert,
} from 'lucide-react';

const STATUS_BADGE: Record<string, string> = {
  FINISHED: 'bg-green-100 text-green-700',
  FAILED: 'bg-red-100 text-red-700',
  RUNNING: 'bg-blue-100 text-blue-700',
  DISPATCHED: 'bg-blue-100 text-blue-700',
  QUEUED: 'bg-gray-100 text-gray-600',
  CANCELED: 'bg-yellow-100 text-yellow-700',
};

const RISK_BADGE: Record<string, string> = {
  HIGH: 'bg-red-100 text-red-700',
  MEDIUM: 'bg-yellow-100 text-yellow-700',
  LOW: 'bg-green-100 text-green-700',
  UNKNOWN: 'bg-gray-100 text-gray-500',
};

function formatDuration(seconds: number | null): string {
  if (seconds == null) return '-';
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return `${h}h ${m}m`;
}

function formatTime(iso: string | null): string {
  if (!iso) return '-';
  return new Date(iso).toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

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
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h2 className="text-2xl font-semibold text-gray-900 mb-1">Test Results</h2>
        <p className="text-sm text-gray-400">Overview of test run statistics and risk distribution</p>
      </div>

      {/* Stat cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard
          label="Total Runs"
          value={stats?.total}
          icon={<ListChecks size={18} className="text-gray-500" />}
          isLoading={isLoading}
        />
        <StatCard
          label="Finished"
          value={stats?.finished}
          icon={<CheckCircle size={18} className="text-green-500" />}
          isLoading={isLoading}
        />
        <StatCard
          label="Failed"
          value={stats?.failed}
          icon={<XCircle size={18} className="text-red-500" />}
          isLoading={isLoading}
        />
        <StatCard
          label="Running"
          value={stats?.running}
          icon={<PlayCircle size={18} className="text-blue-500" />}
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
            <div className="py-8 text-center text-sm text-muted-foreground">
              No test runs yet
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
                      onClick={() => navigate(`/tasks`)}
                    >
                      <td className="py-2 pr-4 font-mono text-xs">#{run.run_id}</td>
                      <td className="py-2 pr-4 truncate max-w-[180px]">{run.task_name}</td>
                      <td className="py-2 pr-4">
                        <span className="px-1.5 py-0.5 rounded text-xs font-medium bg-gray-100 text-gray-700">
                          {run.task_type}
                        </span>
                      </td>
                      <td className="py-2 pr-4">
                        <span className={`px-1.5 py-0.5 rounded text-xs font-medium ${STATUS_BADGE[run.status] ?? 'bg-gray-100 text-gray-500'}`}>
                          {run.status}
                        </span>
                      </td>
                      <td className="py-2 pr-4">
                        <span className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-xs font-medium ${RISK_BADGE[run.risk_level] ?? RISK_BADGE.UNKNOWN}`}>
                          {run.risk_level === 'HIGH' && <ShieldAlert size={10} />}
                          {run.risk_level}
                        </span>
                      </td>
                      <td className="py-2 pr-4 text-xs text-muted-foreground">
                        {formatDuration(run.duration_seconds)}
                      </td>
                      <td className="py-2 text-xs text-muted-foreground">
                        {formatTime(run.started_at)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
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
