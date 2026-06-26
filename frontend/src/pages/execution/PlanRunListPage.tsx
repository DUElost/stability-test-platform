import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { CardContent } from '@/components/ui/card';
import { StatusBadge } from '@/components/ui/status-badge';
import { api } from '@/utils/api';
import { planRunKeys } from '@/utils/api/queryKeys';
import { Clock } from 'lucide-react';
import { PageContainer, PageHeader } from '@/components/layout';
import { LoadingGrid, CardSkeleton } from '@/components/ui/loading-skeleton';
import { EmptyState } from '@/components/ui/empty-state';
import { ClickableCard } from '@/components/ui/clickable-card';
import { TEXT } from '@/design-system/tokens';
import { formatDateTimeFull } from '@/utils/format';

export default function PlanRunListPage() {
  const navigate = useNavigate();

  const { data: runs, isLoading } = useQuery({
    queryKey: planRunKeys.list(),
    queryFn: () => api.planRuns.list(0, 50),
    refetchInterval: 15_000,
  });

  return (
    <PageContainer width="list">
      <PageHeader title="Plan 执行记录" subtitle="查看所有 PlanRun 历史记录" />

      {isLoading ? (
        <LoadingGrid count={2} columns={1} component={CardSkeleton} />
      ) : !runs || runs.length === 0 ? (
        <EmptyState
          title="暂无执行记录"
          description="还没有 Plan 执行记录"
          icon={<Clock className="w-16 h-16" />}
        />
      ) : (
        <div className="space-y-2">
          {runs.map(run => (
            <ClickableCard
              key={run.id}
              onClick={() => navigate(`/execution/plan-runs/${run.id}`)}
              ariaLabel={`查看 Plan Run #${run.id}`}
            >
              <CardContent className="py-3 flex items-center justify-between">
                <div className="flex items-center gap-4">
                  <span className={`font-mono text-sm ${TEXT.subtitle}`}>#{run.id}</span>
                  <StatusBadge kind="plan-run" status={run.status} size="sm" />
                  <span className={`text-sm ${TEXT.heading}`}>
                    {run.plan_name || `Plan #${run.plan_id}`}
                  </span>
                  <span className={`text-xs ${TEXT.caption}`}>{run.run_type}</span>
                </div>
                <div className={`flex items-center gap-4 text-xs ${TEXT.caption}`}>
                  {run.triggered_by && <span>{run.triggered_by}</span>}
                  <span>{formatDateTimeFull(run.started_at)}</span>
                </div>
              </CardContent>
            </ClickableCard>
          ))}
        </div>
      )}
    </PageContainer>
  );
}
