import { useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Button } from '@/components/ui/button';
import { StatusBadge } from '@/components/ui/status-badge';
import { StateTabs } from '@/components/ui/state-tabs';
import {
  FilterSearchInput,
  FilterWorkbench,
  MoreFiltersPopover,
  type ActiveFilterChip,
} from '@/components/ui/filter-bar';
import { SearchableSelect } from '@/components/ui/searchable-select';
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
import { Clock } from 'lucide-react';
import { PageContainer, PageHeader } from '@/components/layout';
import { PageSkeleton } from '@/components/ui/loading-skeleton';
import { EmptyState, SearchEmptyState } from '@/components/ui/empty-state';
import { ErrorState } from '@/components/ui/error-state';
import { ProjectFilterSelect, ProjectKeyBadge } from '@/components/project/ProjectFilterSelect';
import { DashboardStatCard } from '@/components/dashboard/DashboardStatCard';
import { FORM, KPI_TONE, LAYOUT, STAT, TEXT } from '@/design-system/tokens';
import { cn } from '@/lib/utils';
import { formatDateTimeFull, formatDurationSeconds, parseIsoToDate } from '@/utils/format';
import { useDebouncedValue } from '@/hooks/useDebouncedValue';
import { useUrlFilterParams } from '@/hooks/useUrlFilterState';

/** 列表状态筛选：全部 + 常用终态/运行态；排队合并 QUEUED+PRECHECK */
type StatusFilter = 'all' | 'RUNNING' | 'SUCCESS' | 'PARTIAL_SUCCESS' | 'FAILED' | 'queued';
type TimeRangeFilter = 'all' | 'today' | '7d' | '30d';

const STATUS_TABS: { key: StatusFilter; label: string }[] = [
  { key: 'all', label: '全部' },
  { key: 'RUNNING', label: '运行中' },
  { key: 'SUCCESS', label: '成功' },
  { key: 'PARTIAL_SUCCESS', label: '部分成功' },
  { key: 'FAILED', label: '失败' },
  { key: 'queued', label: '排队中' },
];

function computeStartedAfter(range: TimeRangeFilter): string | undefined {
  if (range === 'all') return undefined;
  const now = new Date();
  if (range === 'today') {
    const start = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    return start.toISOString();
  }
  const days = range === '7d' ? 7 : 30;
  return new Date(now.getTime() - days * 24 * 60 * 60 * 1000).toISOString();
}

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

/** ADR-0048 v1.1（#2982）：通过率=completed/total 纯前端派生显示（不消费任何后端 pass_rate 字段——该字段仍废止）。非终态行 result_summary 为空 →「—」。 */
function formatPassRate(run: PlanRun): string {
  const total = run.result_summary?.total;
  const completed = run.result_summary?.completed;
  if (typeof total !== 'number' || total <= 0 || typeof completed !== 'number') return '—';
  return `${Math.round((completed / total) * 100)}%`;
}

/** 失败设备台数事实（ADR-0048 v1.0 保留，与通过率双口径并存）。 */
function formatFailedDevices(run: PlanRun): string {
  const failed = run.result_summary?.failed;
  return typeof failed === 'number' && failed > 0 ? String(failed) : '—';
}

export default function PlanRunListPage() {
  const navigate = useNavigate();
  const { getString, getPositiveInt, updateParams, clearParams } = useUrlFilterParams();

  // ADR-0029 + URL 持久化：筛选与分页同步到 URL
  const projectKey = getString('project') || undefined;
  const rawStatus = getString('status', 'all');
  const statusFilter: StatusFilter = STATUS_TABS.some((t) => t.key === rawStatus)
    ? (rawStatus as StatusFilter)
    : 'all';
  const search = getString('q', '');
  const debouncedSearch = useDebouncedValue(search, 300);
  const rawRange = getString('range', 'all');
  const timeRange: TimeRangeFilter =
    rawRange === 'today' || rawRange === '7d' || rawRange === '30d' ? rawRange : 'all';
  const runType = getString('runType', 'all');
  // PaginationBar 为 1-based
  const page = getPositiveInt('page', 1);
  const rawPageSize = getPositiveInt('pageSize', 50);
  const pageSize = [20, 50, 100].includes(rawPageSize) ? rawPageSize : 50;

  const skip = (page - 1) * pageSize;
  const q = debouncedSearch.trim() || undefined;
  const startedAfter = useMemo(() => computeStartedAfter(timeRange), [timeRange]);

  const {
    data: listPage,
    isLoading,
    isError,
    error,
    refetch,
  } = useQuery({
    queryKey: [
      ...planRunKeys.list(projectKey, {
        page,
        pageSize,
        status: statusFilter === 'all' ? null : statusFilter,
        q: q ?? null,
      }),
      { range: timeRange === 'all' ? null : timeRange, runType: runType === 'all' ? null : runType },
    ],
    queryFn: () =>
      api.planRuns.listPage({
        skip,
        limit: pageSize,
        projectKey,
        status: statusToApi(statusFilter),
        q,
        runType: runType === 'all' ? undefined : runType,
        startedAfter,
      }),
    refetchInterval: 15_000,
  });

  const isProject404 = isError && toApiError(error).status === 404;
  const runs = listPage?.items ?? [];
  const total = listPage?.total ?? 0;
  const stats = listPage?.stats ?? { total: 0, running: 0, failed: 0 };
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const safePage = Math.min(page, totalPages);
  const setPage = (nextPage: number | ((p: number) => number)) => {
    const resolved = typeof nextPage === 'function' ? nextPage(safePage) : nextPage;
    updateParams({ page: resolved <= 1 ? undefined : resolved });
  };
  const hasActiveFilter =
    statusFilter !== 'all' ||
    Boolean(search.trim()) ||
    timeRange !== 'all' ||
    runType !== 'all' ||
    Boolean(projectKey);
  const showEmpty = !isLoading && !isError && stats.total === 0 && !hasActiveFilter;
  const showFilteredEmpty =
    !isLoading
    && !isError
    && !showEmpty
    && runs.length === 0
    && hasActiveFilter
    && total === 0;

  const projectFilter = (
    <ProjectFilterSelect
      value={projectKey}
      onChange={(key) => updateParams({ project: key }, { resetPage: true })}
      className="w-full sm:w-44"
      testId="plan-run-project-filter"
    />
  );

  // 只为「更多筛选」内部专属的条件渲染 Chip，避免与状态 Tabs / 项目 / 时间 / 搜索重复
  const hiddenChips = useMemo<ActiveFilterChip[]>(() => {
    const chips: ActiveFilterChip[] = [];
    if (runType !== 'all') {
      chips.push({
        key: 'runType',
        label: '类型',
        value: runType,
        onRemove: () => updateParams({ runType: undefined }, { resetPage: true }),
      });
    }
    return chips;
  }, [runType, updateParams]);

  return (
    <PageContainer width="wide" className={LAYOUT.pageGap}>
      <PageHeader
        title="Plan 执行记录"
        subtitle="查看所有 PlanRun 历史记录"
      />

      {/* KPI：项目作用域聚合（不受 status/q 影响）；点击切筛、再点取消 */}
      {!isLoading && !isError && stats.total > 0 && (
        <div className="grid grid-cols-3 gap-4">
          <DashboardStatCard
            label="总数"
            value={stats.total}
            onClick={() => updateParams({ status: undefined }, { resetPage: true })}
            ariaLabel="显示全部执行记录"
            valueClassName={cn(STAT.value, statusFilter === 'all' && KPI_TONE.primary.value)}
          />
          <DashboardStatCard
            label="运行中"
            value={stats.running}
            onClick={() =>
              updateParams(
                { status: statusFilter === 'RUNNING' ? undefined : 'RUNNING' },
                { resetPage: true },
              )
            }
            ariaLabel="筛选运行中"
            valueClassName={cn(STAT.value, KPI_TONE.primary.value)}
          />
          <DashboardStatCard
            label="失败"
            value={stats.failed}
            onClick={() =>
              updateParams(
                { status: statusFilter === 'FAILED' ? undefined : 'FAILED' },
                { resetPage: true },
              )
            }
            ariaLabel="筛选失败"
            valueClassName={cn(
              STAT.value,
              stats.failed > 0 ? KPI_TONE.destructive.value : undefined,
            )}
          />
        </div>
      )}

      {/* 统一筛选工作台：顶部左侧状态分段与右侧总数同排，下方收纳次级筛选控件 */}
      <FilterWorkbench
        headerLeft={
          <StateTabs
            items={STATUS_TABS.map((t) => ({
              key: t.key,
              label: t.label,
              testId: `plan-run-status-${t.key}`,
            }))}
            activeKey={statusFilter}
            onChange={(key) =>
              updateParams({ status: key === 'all' ? undefined : key }, { resetPage: true })
            }
            ariaLabel="按状态筛选"
            testId="plan-run-status-tabs"
          />
        }
        hiddenChips={hiddenChips}
        hasAnyActiveFilter={hasActiveFilter}
        onResetAll={() => clearParams(['status', 'project', 'q', 'range', 'runType'])}
        matchSummary={!isLoading && !isError ? `共 ${total} 条执行记录` : undefined}
        chipsTestId="plan-run-filter-chips"
      >
        <div className="flex flex-col gap-2.5 sm:flex-row sm:flex-wrap sm:items-center">
          <FilterSearchInput
            id="plan-run-search"
            value={search}
            onChange={(val) => updateParams({ q: val || undefined }, { resetPage: true })}
            placeholder="搜索 ID / Plan / 触发者..."
            ariaLabel="搜索执行记录"
            testId="plan-run-search"
            className="flex-1 sm:max-w-md"
          />
          {projectFilter}
          <SearchableSelect
            value={timeRange}
            onChange={(val) =>
              updateParams({ range: val === 'all' ? undefined : val }, { resetPage: true })
            }
            options={[
              { value: 'today', label: '今天' },
              { value: '7d', label: '近 7 天' },
              { value: '30d', label: '近 30 天' },
            ]}
            allLabel="全部时间"
            searchable={false}
            icon={<Clock />}
            ariaLabel="按时间范围筛选"
            className="w-full sm:w-36"
            testId="plan-run-time-filter"
          />
          <MoreFiltersPopover
            activeCount={runType !== 'all' ? 1 : 0}
            testId="plan-run-more-filters"
          >
            <div className="space-y-1.5">
              <label className="text-xs font-medium text-foreground">执行类型 (run_type)</label>
              <select
                value={runType}
                onChange={(e) =>
                  updateParams(
                    { runType: e.target.value === 'all' ? undefined : e.target.value },
                    { resetPage: true },
                  )
                }
                aria-label="按执行类型筛选"
                className={cn(FORM.select, 'h-8 w-full text-xs')}
              >
                <option value="all">全部类型</option>
                <option value="MANUAL">手动触发 (MANUAL)</option>
                <option value="SCHEDULE">定时调度 (SCHEDULE)</option>
                <option value="CHAIN">链路触发 (CHAIN)</option>
              </select>
            </div>
          </MoreFiltersPopover>
        </div>
      </FilterWorkbench>

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
            <Button variant="outline" onClick={() => updateParams({ project: undefined })}>
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
          {showFilteredEmpty ? (
            <SearchEmptyState
              keyword={search || STATUS_TABS.find((t) => t.key === statusFilter)?.label || '筛选'}
            />
          ) : (
            <>
              <div className="overflow-x-auto rounded-lg border border-border">
                <Table className="min-w-[960px]">
                  <TableHeader>
                    <TableRow className="border-b text-left text-xs text-muted-foreground hover:bg-transparent">
                      <TableHead className="h-9 px-3">Run</TableHead>
                      <TableHead className="h-9 px-3">状态</TableHead>
                      <TableHead className="h-9 px-3">Plan</TableHead>
                      <TableHead className="h-9 px-3">设备</TableHead>
                      <TableHead className="h-9 px-3">通过率</TableHead>
                      <TableHead className="h-9 px-3">失败设备</TableHead>
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
                        <TableCell className="max-w-[260px] px-3 py-2.5">
                          <span className="flex min-w-0 items-center gap-1.5">
                            <span className={cn('truncate text-sm', TEXT.heading)}>
                              {run.plan_name || `Plan #${run.plan_id}`}
                            </span>
                            <ProjectKeyBadge projectKey={run.project_key} />
                          </span>
                        </TableCell>
                        <TableCell className={cn('px-3 py-2.5 text-xs tabular-nums', TEXT.caption)}>
                          {run.device_count ?? 0}
                        </TableCell>
                        <TableCell className={cn('px-3 py-2.5 text-xs tabular-nums', TEXT.caption)}>
                          {formatPassRate(run)}
                        </TableCell>
                        <TableCell className={cn('px-3 py-2.5 text-xs tabular-nums', TEXT.caption)}>
                          {formatFailedDevices(run)}
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
                  page={safePage}
                  totalPages={totalPages}
                  total={total}
                  pageSize={pageSize}
                  canPreviousPage={safePage > 1}
                  canNextPage={safePage < totalPages}
                  onGoToPage={setPage}
                  onNextPage={() => setPage((p) => p + 1)}
                  onPrevPage={() => setPage((p) => Math.max(1, p - 1))}
                  onChangePageSize={(size) => {
                    updateParams({ pageSize: size === 50 ? undefined : size, page: undefined });
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
