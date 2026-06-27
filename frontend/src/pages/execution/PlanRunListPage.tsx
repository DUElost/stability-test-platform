import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { StatusBadge } from '@/components/ui/status-badge';
import { api } from '@/utils/api';
import { planRunKeys } from '@/utils/api/queryKeys';
import { Clock } from 'lucide-react';
import { PageContainer, PageHeaderV2 } from '@/components/layout';
import { DataList, DataListItem, DataToolbar, DataEmptyState } from '@/components/data';
import { TEXT } from '@/design-system/tokens';
import { cn } from '@/lib/utils';
import { formatDateTimeFull } from '@/utils/format';

export default function PlanRunListPage() {
  const navigate = useNavigate();

  const { data: runs, isLoading } = useQuery({
    queryKey: planRunKeys.list(),
    queryFn: () => api.planRuns.list(0, 50),
    refetchInterval: 15_000,
  });

  return (
    <PageContainer fullBleed>
      <PageHeaderV2 title="Plan 执行记录" description="查看所有 PlanRun 历史记录" />

      <div className="px-6 pb-6 flex-1">
        <DataList
          items={runs ?? []}
          isLoading={isLoading}
          keyExtractor={(run) => String(run.id)}
          header={<DataToolbar searchPlaceholder="搜索执行记录..." />}
          renderItem={(run) => (
            <DataListItem
              onNavigate={() => navigate(`/execution/plan-runs/${run.id}`)}
              actions={<StatusBadge kind="plan-run" status={run.status} size="sm" />}
            >
              <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between w-full gap-2">
                <div className="flex items-center gap-4 min-w-0">
                  <span className={cn('font-mono text-sm', TEXT.subtitle)}>#{run.id}</span>
                  <span className={cn('text-sm', TEXT.heading)}>
                    {run.plan_name || `Plan #${run.plan_id}`}
                  </span>
                  <span className={cn('text-xs', TEXT.caption)}>{run.run_type}</span>
                </div>
                <div className={cn('flex items-center gap-4 text-xs', TEXT.caption)}>
                  {run.triggered_by && <span>{run.triggered_by}</span>}
                  <span>{formatDateTimeFull(run.started_at)}</span>
                </div>
              </div>
            </DataListItem>
          )}
          emptyState={
            <DataEmptyState
              title="暂无执行记录"
              description="还没有 Plan 执行记录"
              icon={<Clock className="w-16 h-16" />}
            />
          }
        />
      </div>
    </PageContainer>
  );
}
