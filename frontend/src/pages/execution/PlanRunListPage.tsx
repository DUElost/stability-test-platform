import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { StatusBadge } from '@/components/ui/status-badge';
import { StateTabs } from '@/components/ui/state-tabs';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { ClickableRow } from '@/components/ui/clickable-row';
import { PaginationBar } from '@/components/ui/pagination-bar';
import { api, toApiError, type PlanRun, type PlanRunStatus } from '@/utils/api';
import { planRunKeys } from '@/utils/api/queryKeys';
import { Clock, Search } from 'lucide-react';
import { PageContainer, PageHeader } from '@/components/layout';
import { PageSkeleton } from '@/components/ui/loading-skeleton';
import { EmptyState, SearchEmptyState } from '@/components/ui/empty-state';
import { ErrorState } from '@/components/ui/error-state';
import { ProjectFilterSelect, ProjectKeyBadge } from '@/components/project/ProjectFilterSelect';
import { DashboardStatCard } from '@/components/dashboard/DashboardStatCard';
import { KPI_TONE, LAYOUT, STAT, TEXT } from '@/design-system/tokens';
import { cn } from '@/lib/utils';
import { formatDateTimeFull, formatDurationSeconds, parseIsoToDate } from '@/utils/format';
import { useDocumentTitle } from '@/hooks/useDocumentTitle';
import { useDebouncedValue } from '@/hooks/useDebouncedValue';

/** 列表状态筛选：全部 + 常用终态/运行态；排队合并 QUEUED+PRECHECK */
type StatusFilter = 'all' | 'RUNNING' | 'SUCCESS' | 'PARTIAL_SUCCESS' | 'FAILED' | 'queued';

const STATUS_TABS: { key: StatusFilter; label: string }[] = [
  { key: 'all', label: '全部' },
  { key: 'RUNNING', label: '运行中' },
  { key: 'SUCCESS', label: '成功' },
  { key: 'PARTIAL_SUCCESS', label: '部分成功' },
  { key: 'FAILED', label: '失败' },
  { key: 'queued', label: '排队中' },
];

function statusToApi(filter: StatusFilter): PlanRunStatus | PlanRunStatus[] | undefined {
  if (filter === 'all') return undefined;
  if (filter === 'queued') return ['QUEUED', 'PRECHECK'];
  return filter;
}

function runDurationSeconds(run: PlanRun): number | null {
  const start = parseIsoToDate(run.started_at);
  if (!start) return null;
  const end = run.ended_at ? parseIsoToDate(run.ended_at) : new Date();
  if (!end) return null;
  return Math.max(0, Math.floor((end.getTime() - start.getTime()) / 1000));
}

export default function PlanRunListPage() {
  useDocumentTitle('Plan 执行记录');
  const navigate = useNavigate();
  // ADR-0029：页面级项目筛选（无全局选择器/跨页跟随）
  const [projectKey, setProjectKey] = useState<string | undefined>(undefined);
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all');
  const [search, setSearch] = useState('');
  const debouncedSearch = useDebouncedValue(search, 300);
  // PaginationBar 为 1-based
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);

  const skip = (page - 1) * pageSize;
  const q = debouncedSearch.trim() || undefined;

  // 搜索词防抖落地后再回第一页（跳过首屏）
  const [searchReady, setSearchReady] = useState(false);
  useEffect(() => {
    if (!searchReady) {
      setSearchReady(true);
      return;
    }
    setPage(1);
  }, [debouncedSearch, searchReady]);

  const {
    data: listPage,
    isLoading,
    isError,
    error,
    refetch,
  } = useQuery({
    queryKey: planRunKeys.list(projectKey, {
      page,
      pageSize,
      status: statusFilter === 'all' ? null : statusFilter,
      q: q ?? null,
    }),
    queryFn: () =>
      api.planRuns.listPage({
        skip,
        limit: pageSize,
        projectKey,
        status: statusToApi(statusFilter),
        q,
      }),
    refetchInterval: 15_000,
  });

  const isProject404 = isError && toApiError(error).status === 404;
  const runs = listPage?.items ?? [];
  const total = listPage?.total ?? 0;
  const stats = listPage?.stats ?? { total: 0, running: 0, failed: 0 };
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const hasActiveFilter = statusFilter !== 'all' || Boolean(search.trim());
  const showEmpty = !isLoading && !isError && stats.total === 0 && !hasActiveFilter;
  const showFilteredEmpty =
    !isLoading && !isError && !showEmpty && runs.length === 0 && hasActiveFilter;
  const hasRunsView = !isLoading && !isError && !showEmpty;

  const resetPage = () => setPage(1);

  const projectFilter = (
    <ProjectFilterSelect
      value={projectKey}
      onChange={(key) => {
        setProjectKey(key);
        resetPage();
      }}
      className="w-44"
      testId="plan-run-project-filter"
    />
  );

  return (
    <PageContainer width="content" className={LAYOUT.pageGap}>
      <PageHeader
        title="Plan 执行记录"
        subtitle="查看所有 PlanRun 历史记录"
      />

      {!hasRunsView && (
        <div className="flex justify-end">{projectFilter}</div>
      )}

      {/* KPI：项目作用域聚合（不受 status/q 影响）；点击切筛 */}
      {!isLoading && !isError && stats.total > 0 && (
        <div className="grid grid-cols-3 gap-4">
          <DashboardStatCard
            label="总数"
            value={stats.total}
            onClick={() => { setStatusFilter('all'); resetPage(); }}
            ariaLabel="显示全部执行记录"
            valueClassName={cn(STAT.value, statusFilter === 'all' && KPI_TONE.primary.value)}
          />
          <DashboardStatCard
            label="运行中"
            value={stats.running}
            onClick={() => { setStatusFilter('RUNNING'); resetPage(); }}
            ariaLabel="筛选运行中"
            valueClassName={cn(STAT.value, KPI_TONE.primary.value)}
          />
          <DashboardStatCard
            label="失败"
            value={stats.failed}
            onClick={() => { setStatusFilter('FAILED'); resetPage(); }}
            ariaLabel="筛选失败"
            valueClassName={cn(
              STAT.value,
              stats.failed > 0 ? KPI_TONE.destructive.value : undefined,
            )}
          />
        </div>
      )}

      {isLoading ? (
        <PageSkeleton>
          <PageSkeleton.Cards count={2} />
        </PageSkeleton>
      ) : isError ? (
        <ErrorState
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
      ) : showEmpty ? (
        <EmptyState
          title="暂无执行记录"
          description={projectKey ? '该项目下还没有执行记录' : '还没有 Plan 执行记录'}
          icon={<Clock className="w-10 h-10" />}
        />
      ) : (
        <div className="space-y-4">
          <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
            <StateTabs
              items={STATUS_TABS.map((t) => ({
                key: t.key,
                label: t.label,
                testId: `plan-run-status-${t.key}`,
              }))}
              activeKey={statusFilter}
              onChange={(key) => {
                setStatusFilter(key as StatusFilter);
                resetPage();
              }}
              ariaLabel="按状态筛选"
              testId="plan-run-status-tabs"
              className="min-w-0 flex-1"
            />
            <div className="flex flex-wrap items-center gap-2 shrink-0">
              {projectFilter}
              <div className="relative w-full sm:w-56">
                <Search className={cn('absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4', TEXT.subtitle)} />
                <Input
                  type="search"
                  placeholder="搜索 ID / Plan / 触发者"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  className="pl-9"
                  data-testid="plan-run-search"
                />
              </div>
            </div>
          </div>

          {showFilteredEmpty ? (
            <SearchEmptyState
              keyword={search || STATUS_TABS.find((t) => t.key === statusFilter)?.label || '筛选'}
            />
          ) : (
            <>
              <div className="overflow-x-auto rounded-lg border border-border">
                <Table className="min-w-[720px]">
                  <TableHeader>
                    <TableRow className="border-b text-left text-xs text-muted-foreground hover:bg-transparent">
                      <TableHead className="h-9 px-3">Run</TableHead>
                      <TableHead className="h-9 px-3">状态</TableHead>
                      <TableHead className="h-9 px-3">Plan</TableHead>
                      <TableHead className="h-9 px-3">类型</TableHead>
                      <TableHead className="h-9 px-3">触发者</TableHead>
                      <TableHead className="h-9 px-3">时长</TableHead>
                      <TableHead className="h-9 px-3">开始时间</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {runs.map((run) => (
                      <ClickableRow
                        key={run.id}
                        className="border-b transition-colors last:border-0 hover:bg-muted/50"
                        onClick={() => navigate(`/execution/plan-runs/${run.id}`)}
                        role="button"
                      >
                        <TableCell className={cn('px-3 py-2.5 font-mono text-xs', TEXT.subtitle)}>
                          #{run.id}
                        </TableCell>
                        <TableCell className="px-3 py-2.5">
                          <StatusBadge kind="plan-run" status={run.status} size="sm" />
                        </TableCell>
                        <TableCell className="max-w-[220px] px-3 py-2.5">
                          <span className="flex min-w-0 items-center gap-1.5">
                            <span className={cn('truncate text-sm', TEXT.heading)}>
                              {run.plan_name || `Plan #${run.plan_id}`}
                            </span>
                            <ProjectKeyBadge projectKey={run.project_key} />
                          </span>
                        </TableCell>
                        <TableCell className={cn('px-3 py-2.5 text-xs', TEXT.caption)}>
                          {run.run_type}
                        </TableCell>
                        <TableCell className={cn('px-3 py-2.5 text-xs', TEXT.caption)}>
                          {run.triggered_by || '—'}
                        </TableCell>
                        <TableCell className={cn('px-3 py-2.5 text-xs', TEXT.caption)}>
                          {formatDurationSeconds(runDurationSeconds(run), 'brief', '—')}
                        </TableCell>
                        <TableCell className={cn('px-3 py-2.5 text-xs whitespace-nowrap', TEXT.caption)}>
                          {formatDateTimeFull(run.started_at)}
                        </TableCell>
                      </ClickableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>

              {total > 0 && (
                <PaginationBar
                  page={page}
                  totalPages={totalPages}
                  total={total}
                  pageSize={pageSize}
                  canPreviousPage={page > 1}
                  canNextPage={page < totalPages}
                  onGoToPage={setPage}
                  onNextPage={() => setPage((p) => p + 1)}
                  onPrevPage={() => setPage((p) => Math.max(1, p - 1))}
                  onChangePageSize={(size) => {
                    setPageSize(size);
                    setPage(1);
                  }}
                  pageSizeOptions={[20, 50, 100]}
                />
              )}
            </>
          )}
        </div>
      )}
    </PageContainer>
  );
}
