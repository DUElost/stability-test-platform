import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Card, CardContent } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { StatusBadge } from '@/components/ui/status-badge';
import { api } from '@/utils/api';
import { Clock } from 'lucide-react';
import { PageContainer, PageHeader } from '@/components/layout';

export default function PlanRunListPage() {
  const navigate = useNavigate();

  const { data: runs, isLoading } = useQuery({
    queryKey: ['plan-runs-list'],
    queryFn: () => api.planRuns.list(0, 50),
    refetchInterval: 15_000,
  });

  return (
    <PageContainer className="max-w-5xl">
      <PageHeader title="Plan 执行记录" subtitle="查看所有 PlanRun 历史记录" />

      {isLoading ? (
        <div className="space-y-3"><Skeleton className="h-16 w-full" /><Skeleton className="h-16 w-full" /></div>
      ) : !runs || runs.length === 0 ? (
        <Card><CardContent className="py-12 text-center text-gray-400">
          <Clock className="w-10 h-10 mx-auto mb-3 text-gray-300" />
          <p className="text-sm">暂无执行记录</p>
        </CardContent></Card>
      ) : (
        <div className="space-y-2">
          {runs.map(run => (
            <Card key={run.id} className="hover:shadow-md transition-shadow cursor-pointer"
              onClick={() => navigate(`/execution/plan-runs/${run.id}`)}>
              <CardContent className="py-3 flex items-center justify-between">
                <div className="flex items-center gap-4">
                  <span className="font-mono text-sm text-gray-500">#{run.id}</span>
                  <StatusBadge kind="plan-run" status={run.status} size="sm" />
                  <span className="text-sm text-gray-700">Plan #{run.plan_id}</span>
                  <span className="text-xs text-gray-400">{run.run_type}</span>
                </div>
                <div className="flex items-center gap-4 text-xs text-gray-400">
                  {run.triggered_by && <span>{run.triggered_by}</span>}
                  <span>{new Date(run.started_at).toLocaleString()}</span>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </PageContainer>
  );
}
