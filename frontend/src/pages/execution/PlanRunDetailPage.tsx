import { useCallback, useEffect, useMemo, useState } from 'react';
import { useLocation, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { ArrowLeft, AlertCircle, AlertTriangle, FileText } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { PageContainer } from '@/components/layout';
import { Skeleton } from '@/components/ui/skeleton';
import { StateTabs } from '@/components/ui/state-tabs';
import { useToast } from '@/hooks/useToast';
import { useDocumentTitle } from '@/hooks/useDocumentTitle';
import PlanRunHero from '@/components/plan-run/PlanRunHero';
import PlanRunKpiGrid, {
  type PlanRunDeviceLinkFilter,
  type PlanRunDeviceStatusFilter,
} from '@/components/plan-run/PlanRunKpiGrid';
import AnomalyDashboard from '@/components/plan-run/AnomalyDashboard';
import PlanChainSidebar from '@/components/plan-run/PlanChainSidebar';
import BusinessFlowStepper from '@/components/plan-run/BusinessFlowStepper';
import DeviceOverview from '@/components/plan-run/DeviceOverview';
import { filterMinimapDevices } from '@/components/plan-run/deviceUiStatus';
import DeviceDetailDrawer from '@/components/plan-run/DeviceDetailDrawer';
import PlanSnapshotDrawer from '@/components/plan-run/PlanSnapshotDrawer';
import DispatchGateCard from '@/components/plan-run/DispatchGateCard';
import ArchiveStatusCard from '@/components/plan-run/ArchiveStatusCard';
import DedupReportCard from '@/components/plan-run/DedupReportCard';
import LogEventsCard from '@/components/plan-run/LogEventsCard';
import JiraRunHistory from '@/components/issues/JiraRunHistory';
import TestCaseResultsCard from '@/components/plan-run/TestCaseResultsCard';
import PrecheckSummaryRow from '@/components/plan-run/PrecheckSummaryRow';
import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogCancel,
  AlertDialogAction,
} from '@/components/ui/alert-dialog';
import { ALERT_BANNER, PANEL, SURFACE, TEXT } from '@/design-system/tokens';
import { cn } from '@/lib/utils';
import { ErrorState } from '@/components/ui/error-state';
import { normalizeWatcherTimeScope, shouldShowDispatchGate, normalizeDispatchStateForRun } from '@/hooks/plan-run/planRunDetailUtils';
import { usePlanRunDetailData } from '@/hooks/plan-run/usePlanRunDetailData';
import { usePlanRunHeaderSlot } from '@/hooks/plan-run/usePlanRunHeaderSlot';
import { api, loadErrorCopy } from '@/utils/api';
import type { WatcherTimeScope } from '@/utils/api/types';

type DetailSectionTab = 'monitoring' | 'delivery' | 'diagnostics';
const TABLE_PAGE_SIZE_OPTIONS = [50, 100, 200] as const;

export default function PlanRunDetailPage() {
  const { runId } = useParams<{ runId: string }>();
  const id = Number(runId);
  const navigate = useNavigate();
  const location = useLocation();
  const toast = useToast();
  const backTarget = (() => {
    const from = (location.state as { from?: unknown } | null)?.from;
    return typeof from === 'string' && from.startsWith('/') ? from : '/execution/plan-runs';
  })();

  const [searchParams, setSearchParams] = useSearchParams();
  const deviceStatusFilter = (searchParams.get('status') ?? 'all') as PlanRunDeviceStatusFilter;
  const deviceLinkFilter = (searchParams.get('link') ?? 'all') as PlanRunDeviceLinkFilter;
  const deviceHostFilter = searchParams.get('host') ?? 'all';
  const deviceSearchQuery = searchParams.get('q') ?? '';
  const deviceViewMode: 'grid' | 'table' = searchParams.get('view') === 'table' ? 'table' : 'grid';
  const rawDevicePage = Number.parseInt(searchParams.get('page') ?? '1', 10);
  const devicePage = Number.isFinite(rawDevicePage) && rawDevicePage >= 1 ? rawDevicePage : 1;
  const rawDeviceLimit = Number.parseInt(searchParams.get('limit') ?? '50', 10);
  const devicePageSize: number = (TABLE_PAGE_SIZE_OPTIONS as readonly number[]).includes(rawDeviceLimit)
    ? rawDeviceLimit
    : 50;
  const watcherTimeScope = normalizeWatcherTimeScope(
    searchParams.get('scope') ?? searchParams.get('window'),
  );
  const rawSection = searchParams.get('section');
  const activeSection: DetailSectionTab =
    rawSection === 'delivery' || rawSection === 'diagnostics' ? rawSection : 'monitoring';
  const drawerParam = searchParams.get('drawer');
  const selectedJobId = drawerParam && /^\d+$/.test(drawerParam) ? Number(drawerParam) : null;

  const updateParam = useCallback(
    (key: string, value: string, isDefault: boolean, resetPage = false) =>
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          if (isDefault) next.delete(key);
          else next.set(key, value);
          if (resetPage) next.delete('page');
          return next;
        },
        { replace: true },
      ),
    [setSearchParams],
  );
  const setDeviceStatusFilter = useCallback(
    (s: PlanRunDeviceStatusFilter) => updateParam('status', s, s === 'all', true),
    [updateParam],
  );
  const setDeviceLinkFilter = useCallback(
    (s: PlanRunDeviceLinkFilter) => updateParam('link', s, s === 'all', true),
    [updateParam],
  );
  const setDeviceHostFilter = useCallback(
    (h: string | 'all') => updateParam('host', h, h === 'all', true),
    [updateParam],
  );
  const setDeviceSearchQuery = useCallback(
    (q: string) => updateParam('q', q, !q.trim(), true),
    [updateParam],
  );
  const setDeviceViewMode = useCallback(
    (mode: 'grid' | 'table') => updateParam('view', mode, mode === 'grid', true),
    [updateParam],
  );
  const setDevicePage = useCallback(
    (nextPage: number) => updateParam('page', String(nextPage), nextPage <= 1),
    [updateParam],
  );
  const setDevicePageSize = useCallback(
    (nextSize: number) => updateParam('limit', String(nextSize), nextSize === 50, true),
    [updateParam],
  );
  const setWatcherTimeScope = useCallback(
    (scope: WatcherTimeScope) => updateParam('scope', scope, scope === 'all'),
    [updateParam],
  );
  const setActiveSection = useCallback(
    (section: DetailSectionTab) => updateParam('section', section, section === 'monitoring'),
    [updateParam],
  );
  const setSelectedJobId = useCallback(
    (jobId: number | null) =>
      updateParam('drawer', jobId == null ? '' : String(jobId), jobId == null),
    [updateParam],
  );
  const resetDeviceFilters = useCallback(
    () =>
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          next.delete('status');
          next.delete('link');
          next.delete('host');
          next.delete('q');
          next.delete('page');
          return next;
        },
        { replace: true },
      ),
    [setSearchParams],
  );

  const [diagOpen, setDiagOpen] = useState(false);
  const [leftPanelOpen, setLeftPanelOpen] = useState(false);
  const [finalArchiveOpen, setFinalArchiveOpen] = useState(false);
  // #3350（ADR-0023 D4）+ #3653 U4：计划快照抽屉（身份 = plan_snapshot；参数 = L2/L3 投影）
  const [snapshotOpen, setSnapshotOpen] = useState(false);
  const [snapshotJobId, setSnapshotJobId] = useState<number | null>(null);

  const {
    runQ,
    timelineQ,
    minimapQ,
    devicesPageQ,
    selectedJobQ,
    watcherQ,
    chainQ,
    jobProjectionQ,
    isTerminal,
    isAnyFetching,
    refreshAll,
    prefetchAdjacentJobs,
    abortMut,
    finalArchiveMut,
    retryMut,
    exitMut,
    retryDispatchMut,
    chainDispatchFailed,
    stuckJobs,
    planName,
  } = usePlanRunDetailData(id, {
    deviceStatusFilter,
    deviceLinkFilter,
    deviceHostFilter,
    deviceSearchQuery,
    viewMode: deviceViewMode,
    devicePage,
    devicePageSize,
    selectedJobId,
    watcherTimeScope,
    projectionJobId: snapshotJobId,
  });

  // B3-3（契约 §5.1 / §5.3）：Minimap 视图按当前筛选全集顺序导航，表格分页视图按本页顺序导航；
  // 通过 `?drawer=` 打开不在当前浏览集合中的设备时不得猜测邻居。
  const filteredMinimapDevices = useMemo(
    () =>
      filterMinimapDevices(minimapQ.data?.devices ?? [], {
        statusFilter: deviceStatusFilter,
        linkFilter: deviceLinkFilter,
        hostFilter: deviceHostFilter,
        searchQuery: deviceSearchQuery,
      }),
    [minimapQ.data?.devices, deviceStatusFilter, deviceLinkFilter, deviceHostFilter, deviceSearchQuery],
  );
  const navJobIds = useMemo(
    () =>
      deviceViewMode === 'table'
        ? (devicesPageQ.data?.devices ?? []).map((d) => d.job_id)
        : filteredMinimapDevices.map((d) => d.job_id),
    [deviceViewMode, devicesPageQ.data?.devices, filteredMinimapDevices],
  );
  const selectedDeviceIdx = useMemo(
    () => (selectedJobId == null ? -1 : navJobIds.indexOf(selectedJobId)),
    [navJobIds, selectedJobId],
  );
  const handlePrevDevice =
    selectedDeviceIdx > 0
      ? () => setSelectedJobId(navJobIds[selectedDeviceIdx - 1])
      : undefined;
  const handleNextDevice =
    selectedDeviceIdx >= 0 && selectedDeviceIdx < navJobIds.length - 1
      ? () => setSelectedJobId(navJobIds[selectedDeviceIdx + 1])
      : undefined;

  // B3-3（契约 §5.3 规则 4）：选中单机详情加载成功后，最多预取相邻 2 条单机详情
  useEffect(() => {
    if (!selectedJobQ.isSuccess || selectedDeviceIdx < 0) return;
    const neighbors: number[] = [];
    if (selectedDeviceIdx > 0) neighbors.push(navJobIds[selectedDeviceIdx - 1]);
    if (selectedDeviceIdx < navJobIds.length - 1) neighbors.push(navJobIds[selectedDeviceIdx + 1]);
    if (neighbors.length > 0) {
      prefetchAdjacentJobs(neighbors);
    }
  }, [selectedJobQ.isSuccess, selectedDeviceIdx, navJobIds, prefetchAdjacentJobs]);

  const finalArchivePromptedKey = `plan-run-${id}-final-archive-prompted`;
  // #780：原先读 `archive.readiness` / `archive.ready_for_extract`——后端
  // `WatcherArchiveOut` 从不产出这两个键（只有 `scan_status`），条件因此恒 false，
  // 归档提示永不弹出。改用真实键，并按后端**可执行性**收口状态判据：
  //   · `trigger_extract` 对 FAILED 无条件 409（ADR-0028 D2，dedup.py）；
  //   · `scan_status === 'merged'` 才可能过 `run_extract_sync` 的「先 merge」前置。
  // 否则提示出来的动作必定失败。
  const finalArchiveReady =
    (runQ.data?.capabilities?.final_archive ?? false) &&
    (runQ.data?.status === 'SUCCESS' || runQ.data?.status === 'PARTIAL_SUCCESS') &&
    watcherQ.data?.archive?.scan_status === 'merged';
  useEffect(() => {
    if (!runQ.data) return;
    if (!finalArchiveReady) return;
    if (sessionStorage.getItem(finalArchivePromptedKey)) return;
    sessionStorage.setItem(finalArchivePromptedKey, '1');
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 一次性归档提示（sessionStorage 防重）
    setFinalArchiveOpen(true);
  }, [runQ.data, finalArchivePromptedKey, finalArchiveReady]);

  const toggleLeftPanel = useCallback(() => setLeftPanelOpen((v) => !v), []);

  const handleRerun = useCallback(async () => {
    const run = runQ.data;
    if (!run) return;
    let deviceIds = run.run_context?.dispatch_device_ids ?? [];
    if (deviceIds.length === 0) {
      try {
        const jobs = await api.planRuns.listJobs(id);
        deviceIds = [...new Set(jobs.map((job) => job.device_id))];
      } catch {
        deviceIds = [];
      }
    }
    if (deviceIds.length === 0) {
      toast.error('未能获取上次执行的设备清单，无法复跑');
      return;
    }
    navigate(`/execution/plan-execute?plan=${run.plan_id}&devices=${deviceIds.join(',')}`);
  }, [id, navigate, runQ.data, toast]);

  usePlanRunHeaderSlot({
    runId: id,
    active: 'overview',
    dataUpdatedAt: runQ.dataUpdatedAt,
    isAnyFetching,
    refreshAll,
    onToggleLeftPanel: toggleLeftPanel,
  });

  useDocumentTitle(planName || runQ.data?.plan_name || (id ? `Plan Run #${id}` : 'Plan Run'));

  if (!id || Number.isNaN(id)) {
    return (
      <PageContainer width="content" className="space-y-3">
        <Button
          variant="ghost"
          size="sm"
          onClick={() => navigate(backTarget)}
        >
          <ArrowLeft className="mr-1 h-4 w-4" /> 返回列表
        </Button>
        <div className={cn('flex h-48 items-center justify-center text-sm', TEXT.subtitle)}>
          <AlertCircle className="mr-2 h-4 w-4" /> 无效 PlanRun ID
        </div>
      </PageContainer>
    );
  }

  if (runQ.isError) {
    const runErrorCopy = loadErrorCopy(runQ.error, {
      notFound: '执行记录不存在或已被清理（可能已过期回收）。',
    });
    return (
      <PageContainer width="content" className="space-y-3">
        <Button
          variant="ghost"
          size="sm"
          onClick={() => navigate(backTarget)}
        >
          <ArrowLeft className="mr-1 h-4 w-4" /> 返回列表
        </Button>
        <ErrorState
          // #2361：404 是「资源不存在」，不是网络问题——服务端 detail 是英文
          // `plan run not found`，直接透出既不本地化也把人引向查网络。
          title={runErrorCopy.retryable ? '加载 PlanRun 失败' : '执行记录不存在'}
          description={runErrorCopy.description}
          onRetry={runErrorCopy.retryable ? () => runQ.refetch() : undefined}
        />
      </PageContainer>
    );
  }

  const precheck = runQ.data?.run_context?.precheck ?? null;
  const dispatchStateRaw = normalizeDispatchStateForRun(
    runQ.data,
    runQ.data?.run_context?.dispatch_state ?? null,
  );
  const resultSummary = runQ.data?.result_summary;
  const admissionDispatchFailed =
    runQ.data?.status === 'FAILED' &&
    (resultSummary?.dispatch_failed === true ||
      resultSummary?.precheck_failed === true);
  const dispatchState =
    dispatchStateRaw?.status === 'failed' || !admissionDispatchFailed
      ? dispatchStateRaw
      : {
          ...dispatchStateRaw,
          status: 'failed' as const,
          last_error: String(
            resultSummary?.reason ?? resultSummary?.error ?? 'dispatch_failed',
          ),
        };
  const dispatchFailed =
    dispatchState?.status === 'failed' || admissionDispatchFailed;
  const gateFailed = precheck?.phase === 'failed' || dispatchFailed;
  const showDispatchGate = shouldShowDispatchGate(runQ.data);
  const showDiag = diagOpen || gateFailed;

  return (
    <PageContainer width="bleed" scrollable={false} className={cn('flex h-full flex-col overflow-hidden', SURFACE.page)}>
      <div className="flex flex-1 min-h-0 overflow-hidden">
        {leftPanelOpen && (
          <div
            data-testid="left-panel-backdrop"
            className="fixed inset-x-0 bottom-0 top-20 z-30 bg-foreground/30 lg:hidden"
            onClick={() => setLeftPanelOpen(false)}
          />
        )}
        <aside
          className={cn(
            'flex w-72 shrink-0 flex-col overflow-hidden border-r border-border bg-card transition-transform fixed bottom-0 left-0 top-20 z-40 shadow-xl lg:static lg:bottom-auto lg:top-auto lg:z-auto lg:shadow-none',
            leftPanelOpen ? 'translate-x-0' : '-translate-x-full',
            'lg:translate-x-0',
          )}
        >
          {/* Hero（含导出/中止）固定侧栏顶：原位可见，不被 KPI/链滚动遮挡 */}
          <div className="shrink-0 border-b border-border p-3 pb-3">
            {runQ.isLoading ? (
              <Skeleton className="h-36 w-full rounded-lg" />
            ) : (
              <PlanRunHero
                run={runQ.data}
                planName={planName}
                isAborting={abortMut.isPending}
                onAbort={(reason) => abortMut.mutate(reason)}
                onRerun={() => void handleRerun()}
                onViewSnapshot={() => setSnapshotOpen(true)}
                onExportReport={async (format) => {
                  try {
                    const blob = await api.planRuns.exportReport(id, format);
                    const ext = format === 'json' ? 'json' : 'md';
                    const url = URL.createObjectURL(blob);
                    const anchor = document.createElement('a');
                    anchor.href = url;
                    anchor.download = `plan-run-${id}-report.${ext}`;
                    anchor.click();
                    URL.revokeObjectURL(url);
                    toast.success('PlanRun 报告已导出');
                  } catch (err: unknown) {
                    const msg = err instanceof Error ? err.message : String(err);
                    toast.error(`导出失败: ${msg}`);
                  }
                }}
              />
            )}
          </div>
          <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto p-3">
            <div className="shrink-0">
              <PlanRunKpiGrid
                facets={minimapQ.data?.facets}
                currentStage={timelineQ.data?.current_stage ?? null}
                patrolCycle={
                  timelineQ.data?.stages?.find((s) => s.stage === 'patrol')
                    ?.patrol_cycle_index ?? null
                }
                statusFilter={deviceStatusFilter}
                linkFilter={deviceLinkFilter}
                onSelectStatus={setDeviceStatusFilter}
                onSelectLink={setDeviceLinkFilter}
                onResetFilters={resetDeviceFilters}
              />
            </div>
            <div className="shrink-0">
              <PlanChainSidebar
                chain={chainQ.data}
                isLoading={chainQ.isLoading}
                isError={chainQ.isError}
                chainDispatchFailed={chainDispatchFailed}
                onNavigateRun={(planRunId) => navigate(`/execution/plan-runs/${planRunId}`)}
              />
            </div>
          </div>
        </aside>

        <div className="flex flex-1 flex-col min-h-0 overflow-hidden">
          {stuckJobs.length > 0 && (
            <div
              data-testid="stuck-jobs-banner"
              className={cn('flex shrink-0 items-start gap-2 px-4 py-2.5 text-xs', ALERT_BANNER.warning)}
            >
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-warning" />
              <div className="min-w-0 space-y-1">
                <p className="font-semibold">
                  {stuckJobs.length} 个 Job 心跳超时，可能已断开
                </p>
                <p className="text-xs text-warning/90">
                  后端 recycler 将把超时 Job 标记为 UNKNOWN；grace 窗口内 Agent 可通过 recovery 恢复。
                  设备：
                  {stuckJobs
                    .map((d) => d.device_serial || `#${d.device_id}`)
                    .join('、')}
                </p>
              </div>
            </div>
          )}

          <div className="flex-1 overflow-y-auto p-3 space-y-3">
            {/* Minimap 与设备态势常驻置顶（Owner 硬保留约束） */}
            <DeviceOverview
              minimapData={minimapQ.data}
              tableData={devicesPageQ.data}
              isLoading={minimapQ.isLoading}
              isError={minimapQ.isError}
              tableLoading={devicesPageQ.isLoading}
              tableError={devicesPageQ.isError}
              isFetchingTable={devicesPageQ.isFetching}
              viewMode={deviceViewMode}
              onViewModeChange={setDeviceViewMode}
              statusFilter={deviceStatusFilter}
              linkFilter={deviceLinkFilter}
              hostFilter={deviceHostFilter}
              searchQuery={deviceSearchQuery}
              onStatusFilterChange={setDeviceStatusFilter}
              onLinkFilterChange={setDeviceLinkFilter}
              onHostFilterChange={setDeviceHostFilter}
              onSearchQueryChange={setDeviceSearchQuery}
              page={devicePage}
              pageSize={devicePageSize}
              pageSizeOptions={TABLE_PAGE_SIZE_OPTIONS}
              onPageChange={setDevicePage}
              onPageSizeChange={setDevicePageSize}
              onSelectJobId={setSelectedJobId}
              selectedJobId={selectedJobId}
            />

            <StateTabs
              variant="underline"
              ariaLabel="执行详情分区"
              testId="plan-run-section-tabs"
              activeKey={activeSection}
              onChange={(key) => setActiveSection(key as DetailSectionTab)}
              items={[
                {
                  key: 'monitoring',
                  label: '异常与归档',
                  panelId: 'plan-run-panel-monitoring',
                  testId: 'plan-run-section-monitoring',
                },
                {
                  key: 'delivery',
                  label: isTerminal ? '日志与交付' : '日志与交付（归档）',
                  panelId: 'plan-run-panel-delivery',
                  testId: 'plan-run-section-delivery',
                },
                {
                  key: 'diagnostics',
                  label: gateFailed ? '派发与诊断（异常）' : '派发与诊断',
                  panelId: 'plan-run-panel-diagnostics',
                  testId: 'plan-run-section-diagnostics',
                },
              ]}
            />

            {activeSection === 'monitoring' && (
              <div
                role="tabpanel"
                id="plan-run-panel-monitoring"
                className="space-y-3"
              >
                <AnomalyDashboard
                  runId={id}
                  data={watcherQ.data}
                  isLoading={watcherQ.isLoading}
                  isError={watcherQ.isError}
                  timeScope={watcherTimeScope}
                  onTimeScopeChange={setWatcherTimeScope}
                  onSelectDeviceSerial={(serial) => {
                    // B3-3（契约 §5.1 规则 5）：Watcher 串号反查复用 Minimap 全集轻量索引，不依赖单页表格
                    const matched = minimapQ.data?.devices.find(
                      (d) => d.device_serial === serial,
                    );
                    if (matched) {
                      setSelectedJobId(matched.job_id);
                    } else {
                      navigate(
                        `/execution/plan-runs/${id}/logs?q=${encodeURIComponent(serial)}`,
                      );
                    }
                  }}
                />

                <ArchiveStatusCard
                  opsMetrics={watcherQ.data?.archive?.ops_metrics}
                  linkStats={watcherQ.data?.archive?.link_stats}
                  isLoading={watcherQ.isLoading}
                  isError={watcherQ.isError}
                  onRetry={() => void watcherQ.refetch()}
                />
              </div>
            )}

            {activeSection === 'delivery' && (
              <div
                role="tabpanel"
                id="plan-run-panel-delivery"
                className="space-y-3"
              >
                {/* #529：终态 DLE 事件视图（归档权威）。RUNNING 不渲染，见组件 gate。 */}
                {isTerminal && <LogEventsCard runId={id} isTerminal />}
                {isTerminal && <TestCaseResultsCard runId={id} isTerminal />}

                <DedupReportCard
                  runId={id}
                  uploadSummary={runQ.data?.run_context?.upload_summary}
                  extractSummary={runQ.data?.run_context?.extract}
                  mergePlatforms={runQ.data?.run_context?.merge_platforms}
                />

                <JiraRunHistory planRunId={id} />
              </div>
            )}

            {activeSection === 'diagnostics' && (
              <div
                role="tabpanel"
                id="plan-run-panel-diagnostics"
                className="space-y-3"
              >
                <BusinessFlowStepper
                  timeline={timelineQ.data}
                  isLoading={timelineQ.isLoading}
                  isError={timelineQ.isError}
                />

                {showDispatchGate ? (
                  <section data-testid="dispatch-gate-section" className="space-y-2">
                    {precheck && (
                      <PrecheckSummaryRow
                        precheck={precheck}
                        expanded={showDiag}
                        onToggle={() => setDiagOpen((v) => !v)}
                        gateFailed={gateFailed}
                      />
                    )}
                    <div className={!precheck || showDiag ? '' : 'hidden'}>
                      <DispatchGateCard
                        precheck={precheck}
                        dispatchState={dispatchState}
                        isTerminal={isTerminal}
                        onRetryDispatch={() => retryDispatchMut.mutate()}
                        isRetrying={retryDispatchMut.isPending}
                        retryable={runQ.data?.capabilities?.retry_dispatch}
                      />
                    </div>
                  </section>
                ) : (
                  <div className={cn(PANEL.root, 'p-4 text-xs', TEXT.subtitle)}>
                    当前 PlanRun 无需额外的派发门禁诊断信息。
                  </div>
                )}

                <div className={cn(PANEL.root, 'flex flex-wrap items-center justify-between gap-3 p-4')}>
                  <div className="min-w-0">
                    <div className={cn('text-sm font-semibold', TEXT.heading)}>
                      冻结计划快照与参数投影（L2 / L3）
                    </div>
                    <p className={cn('mt-0.5 text-xs', TEXT.subtitle)}>
                      查看本次执行冻结的 Plan 步序、有效参数投影及单设备 L3 下发参数。
                    </p>
                  </div>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={() => setSnapshotOpen(true)}
                    disabled={!runQ.data}
                  >
                    <FileText className="mr-1.5 h-3.5 w-3.5" />
                    打开计划快照
                  </Button>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>

      <DeviceDetailDrawer
        open={selectedJobId != null}
        jobId={selectedJobId}
        device={selectedJobQ.data ?? null}
        isLoading={selectedJobQ.isLoading}
        isError={selectedJobQ.isError}
        onRetryLoad={() => void selectedJobQ.refetch()}
        runId={id}
        onClose={() => setSelectedJobId(null)}
        onManualRetry={(jobId) => retryMut.mutate(jobId)}
        onManualExit={(jobId) => exitMut.mutate(jobId)}
        // #2420：这里两个 id 都在手上，带上 planRun 让报告端点做归属校验。
        // 只有拿到数字型 run id 才加——拿不到时宁可不校验，也不能生成 `planRun=undefined`
        // 那种"看起来带了参数、实际会把后端判成错配"的假校验。
        onOpenReport={(jobId) =>
          navigate(
            /^\d+$/.test(String(id ?? ''))
              ? `/jobs/${jobId}/report?planRun=${id}`
              : `/jobs/${jobId}/report`,
          )
        }
        isRetryPending={retryMut.isPending && retryMut.variables === selectedJobId}
        isExitPending={exitMut.isPending && exitMut.variables === selectedJobId}
        devicePosition={selectedDeviceIdx >= 0 ? selectedDeviceIdx + 1 : undefined}
        totalDevices={selectedDeviceIdx >= 0 ? navJobIds.length : undefined}
        navScopeLabel={deviceViewMode === 'table' ? '本页' : undefined}
        onPrevDevice={handlePrevDevice}
        onNextDevice={handleNextDevice}
      />

      {/* #3350 + #3653 U4 + B3-3：快照设备选择器复用 Minimap 全集轻量索引，不随表格翻页截断 */}
      <PlanSnapshotDrawer
        open={snapshotOpen}
        onClose={() => {
          setSnapshotOpen(false);
          setSnapshotJobId(null);
        }}
        snapshot={runQ.data?.plan_snapshot ?? null}
        l2Projection={runQ.data?.parameter_projection ?? null}
        l2Unavailable={Boolean(runQ.isSuccess && runQ.data?.parameter_projection == null)}
        devices={minimapQ.data?.devices ?? []}
        selectedJobId={snapshotJobId}
        onSelectJobId={setSnapshotJobId}
        l3Projection={jobProjectionQ.data ?? null}
        l3Loading={snapshotJobId != null && jobProjectionQ.isFetching && !jobProjectionQ.data}
        l3Unavailable={Boolean(
          snapshotJobId != null && jobProjectionQ.isError,
        )}
      />

      <AlertDialog open={finalArchiveOpen} onOpenChange={setFinalArchiveOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>PlanRun 已结束 — 是否最终归档？</AlertDialogTitle>
            <AlertDialogDescription>
              检测到 scan + merge 已完成，是否继续执行分类提取（按去重结果从
              中心存储取事件日志到提单目录）？
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>暂不归档</AlertDialogCancel>
            <AlertDialogAction
              data-testid="final-archive-confirm"
              disabled={finalArchiveMut.isPending}
              onClick={() => {
                setFinalArchiveOpen(false);
                finalArchiveMut.mutate();
              }}
            >
              {finalArchiveMut.isPending ? '提取中…' : '执行最终归档'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </PageContainer>
  );
}
