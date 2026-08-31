import { useMemo, useState, type KeyboardEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { StatusBadge } from '@/components/ui/status-badge';
import { api, toApiError } from '@/utils/api';
import { planRunKeys } from '@/utils/api/queryKeys';
import { Clock } from 'lucide-react';
import { PageContainer, PageHeader } from '@/components/layout';
import { PageSkeleton } from '@/components/ui/loading-skeleton';
import { EmptyState } from '@/components/ui/empty-state';
import { ErrorState } from '@/components/ui/error-state';
import { ProjectFilterSelect, ProjectKeyBadge } from '@/components/project/ProjectFilterSelect';
import { STAT, TEXT } from '@/design-system/tokens';
import { cn } from '@/lib/utils';
import { formatDateTimeFull } from '@/utils/format';
import { useDocumentTitle } from '@/hooks/useDocumentTitle';

export default function PlanRunListPage() {
  useDocumentTitle('Plan 执行记录');
  const navigate = useNavigate();
  // ADR-0029：页面级项目筛选（无全局选择器/跨页跟随）
  const [projectKey, setProjectKey] = useState<string | undefined>(undefined);

  const {
    data: runs,
    isLoading,
    isError,
    error,
    refetch,
  } = useQuery({
    queryKey: planRunKeys.list(projectKey),
    queryFn: () => api.planRuns.list(0, 50, undefined, undefined, projectKey),
    refetchInterval: 15_000,
  });

  const isProject404 = isError && toApiError(error).status === 404;

  const stats = useMemo(() => {
    const list = runs ?? [];
    return {
      total: list.length,
      running: list.filter((r) => r.status === 'RUNNING').length,
      failed: list.filter((r) => r.status === 'FAILED').length,
    };
  }, [runs]);

  return (
    <PageContainer width="content">
      <PageHeader
        title="Plan 执行记录"
        subtitle="查看所有 PlanRun 历史记录"
        action={
          <ProjectFilterSelect
            value={projectKey}
            onChange={setProjectKey}
            className="w-52"
            testId="plan-run-project-filter"
          />
        }
      />

      {/* Stats — 稀疏数字条（当前页聚合，不新增 API） */}
      {!isLoading && !isError && runs && runs.length > 0 && (
        <div className="grid grid-cols-3 gap-3">
          <Card>
            <CardContent className="py-4 px-4">
              <p className={STAT.label}>总数</p>
              <p className={cn(STAT.value, 'mt-2')}>{stats.total}</p>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="py-4 px-4">
              <p className={STAT.label}>运行中</p>
              <p className={cn(STAT.value, 'mt-2')}>{stats.running}</p>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="py-4 px-4">
              <p className={STAT.label}>失败</p>
              <p className={cn(STAT.value, 'mt-2')}>{stats.failed}</p>
            </CardContent>
          </Card>
        </div>
      )}

      {isLoading ? (
        <PageSkeleton>
          <PageSkeleton.Cards count={2} />
        </PageSkeleton>
      ) : isError ? (
        <ErrorState
          // 未知项目 key：按错误态渲染（后端统一 404），不吞成空列表
          title={isProject404 ? '项目不存在' : '加载执行记录失败'}
          description={isProject404
            ? `项目 "${projectKey}" 不存在，请清除筛选或核对 key`
            : toApiError(error).message || '请检查网络连接或稍后重试'}
          onRetry={isProject404 ? undefined : () => void refetch()}
          action={isProject404 ? (
            <Button variant="outline" onClick={() => setProjectKey(undefined)}>
              清除项目筛选
            </Button>
          ) : undefined}
        />
      ) : !runs || runs.length === 0 ? (
        <EmptyState
          title="暂无执行记录"
          description={projectKey ? '该项目下还没有执行记录' : '还没有 Plan 执行记录'}
          icon={<Clock className="w-10 h-10" />}
        />
      ) : (
        <div className="space-y-2">
          {runs.map(run => {
            const go = () => navigate(`/execution/plan-runs/${run.id}`);
            const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                go();
              }
            };
            return (
              <Card
                key={run.id}
                className={cn(
                  'cursor-pointer transition-colors hover:bg-muted/30',
                  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2',
                )}
                onClick={go}
                onKeyDown={onKeyDown}
                tabIndex={0}
                role="button"
                aria-label={`查看 Plan Run #${run.id}`}
              >
                <CardContent className="py-3 flex items-center justify-between">
                  <div className="flex items-center gap-4">
                    <span className={`font-mono text-sm ${TEXT.subtitle}`}>#{run.id}</span>
                    <StatusBadge kind="plan-run" status={run.status} size="sm" />
                    <span className={`min-w-0 truncate text-sm ${TEXT.heading}`}>
                      {run.plan_name || `Plan #${run.plan_id}`}
                    </span>
                    <ProjectKeyBadge projectKey={run.project_key} />
                    <span className={`text-xs ${TEXT.caption}`}>{run.run_type}</span>
                  </div>
                  <div className={`flex items-center gap-4 text-xs ${TEXT.caption}`}>
                    {run.triggered_by && <span>{run.triggered_by}</span>}
                    <span>{formatDateTimeFull(run.started_at)}</span>
                  </div>
                </CardContent>
              </Card>
            );
          })}
        </div>
      )}
    </PageContainer>
  );
}
