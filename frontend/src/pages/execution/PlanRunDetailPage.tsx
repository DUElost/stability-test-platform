import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { ArrowLeft, AlertCircle, AlertTriangle } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { useToast } from '@/hooks/useToast';
import { useDocumentTitle } from '@/hooks/useDocumentTitle';
import PlanRunHero from '@/components/plan-run/PlanRunHero';
import PlanRunKpiGrid from '@/components/plan-run/PlanRunKpiGrid';
import AnomalyDashboard from '@/components/plan-run/AnomalyDashboard';
import PlanChainSidebar from '@/components/plan-run/PlanChainSidebar';
import BusinessFlowStepper from '@/components/plan-run/BusinessFlowStepper';
import DeviceOverview from '@/components/plan-run/DeviceOverview';
import DeviceDetailDrawer from '@/components/plan-run/DeviceDetailDrawer';
import DispatchGateCard from '@/components/plan-run/DispatchGateCard';
import ArchiveStatusCard from '@/components/plan-run/ArchiveStatusCard';
import DedupReportCard from '@/components/plan-run/DedupReportCard';
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
import { ALERT_BANNER, SURFACE, TEXT } from '@/design-system/tokens';
import { cn } from '@/lib/utils';
import { ErrorState } from '@/components/ui/error-state';
import { normalizeWatcherTimeScope } from '@/hooks/plan-run/planRunDetailUtils';
import { usePlanRunDetailData } from '@/hooks/plan-run/usePlanRunDetailData';
import { usePlanRunHeaderSlot } from '@/hooks/plan-run/usePlanRunHeaderSlot';
import { api } from '@/utils/api';
import type { DeviceUiStatus, WatcherTimeScope } from '@/utils/api/types';

export default function PlanRunDetailPage() {
  const { runId } = useParams<{ runId: string }>();
  const id = Number(runId);
  const navigate = useNavigate();
  const toast = useToast();

  const [searchParams, setSearchParams] = useSearchParams();
  const deviceStatusFilter = (searchParams.get('status') ?? 'all') as DeviceUiStatus | 'all';
  const deviceHostFilter = searchParams.get('host') ?? 'all';
  const watcherTimeScope = normalizeWatcherTimeScope(
    searchParams.get('scope') ?? searchParams.get('window'),
  );

  const updateParam = useCallback(
    (key: string, value: string, isDefault: boolean) =>
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          if (isDefault) next.delete(key);
          else next.set(key, value);
          return next;
        },
        { replace: true },
      ),
    [setSearchParams],
  );
  const setDeviceStatusFilter = useCallback(
    (s: DeviceUiStatus | 'all') => updateParam('status', s, s === 'all'),
    [updateParam],
  );
  const setDeviceHostFilter = useCallback(
    (h: string | 'all') => updateParam('host', h, h === 'all'),
    [updateParam],
  );
  const setWatcherTimeScope = useCallback(
    (scope: WatcherTimeScope) => updateParam('scope', scope, scope === 'all'),
    [updateParam],
  );

  const [selectedJobId, setSelectedJobId] = useState<number | null>(null);
  const [diagOpen, setDiagOpen] = useState(false);
  const [leftPanelOpen, setLeftPanelOpen] = useState(false);
  const [finalArchiveOpen, setFinalArchiveOpen] = useState(false);

  const {
    runQ,
    timelineQ,
    devicesQ,
    watcherQ,
    chainQ,
    isTerminal,
    isAnyFetching,
    refreshAll,
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
    deviceHostFilter,
    watcherTimeScope,
  });

  const selectedDevice = useMemo(
    () =>
      selectedJobId == null
        ? null
        : devicesQ.data?.devices.find((device) => device.job_id === selectedJobId) ?? null,
    [devicesQ.data?.devices, selectedJobId],
  );

  const finalArchivePromptedKey = `plan-run-${id}-final-archive-prompted`;
  const archiveReadiness = watcherQ.data?.archive?.readiness;
  const archiveDataReady =
    archiveReadiness?.ready ??
    watcherQ.data?.archive?.ready_for_extract ??
    false;
  const finalArchiveReady =
    archiveDataReady &&
    (runQ.data?.capabilities?.final_archive ?? false);
  useEffect(() => {
    if (!runQ.data) return;
    const status = runQ.data.status;
    if (status !== 'FAILED' && status !== 'DEGRADED') return;
    if (!finalArchiveReady) return;
    if (sessionStorage.getItem(finalArchivePromptedKey)) return;
    sessionStorage.setItem(finalArchivePromptedKey, '1');
    setFinalArchiveOpen(true);
  }, [runQ.data?.status, finalArchivePromptedKey, finalArchiveReady]);

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
    dataUpdatedAt: runQ.dataUpdatedAt,
    isAnyFetching,
    refreshAll,
    onToggleLeftPanel: toggleLeftPanel,
  });

  useDocumentTitle(planName || runQ.data?.plan_name || (id ? `Plan Run #${id}` : 'Plan Run'));

  if (!id || Number.isNaN(id)) {
    return (
      <div className={cn('flex h-64 items-center justify-center text-sm', TEXT.subtitle)}>
        <AlertCircle className="mr-2 h-4 w-4" /> 无效 PlanRun ID
      </div>
    );
  }

  if (runQ.isError) {
    return (
      <div className="space-y-3">
        <Button
          variant="ghost"
          size="sm"
          onClick={() => navigate('/execution/plan-runs')}
        >
          <ArrowLeft className="mr-1 h-4 w-4" /> 返回列表
        </Button>
        <ErrorState
          title="加载 PlanRun 失败"
          description={(runQ.error as Error)?.message || '请检查网络连接或稍后重试'}
          onRetry={() => runQ.refetch()}
        />
      </div>
    );
  }

  const precheck = runQ.data?.run_context?.precheck ?? null;
  const dispatchState = runQ.data?.run_context?.dispatch_state ?? null;
  const dispatchFailed = dispatchState?.status === 'failed';
  const gateFailed =
    precheck?.phase === 'failed' || dispatchFailed;
  const showDiag = diagOpen || gateFailed;

  return (
    <div className={cn('flex h-full flex-col overflow-hidden', SURFACE.page)}>
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
            'flex w-72 shrink-0 flex-col gap-4 overflow-y-auto border-r border-border bg-card p-4 transition-transform fixed bottom-0 left-0 top-20 z-40 shadow-xl lg:static lg:bottom-auto lg:top-auto lg:z-auto lg:shadow-none',
            leftPanelOpen ? 'translate-x-0' : '-translate-x-full',
            'lg:translate-x-0',
          )}
        >
          {runQ.isLoading ? (
            <Skeleton className="h-36 w-full rounded-xl" />
          ) : (
            <PlanRunHero
              run={runQ.data}
              planName={planName}
              isAborting={abortMut.isPending}
              onAbort={(reason) => abortMut.mutate(reason)}
              onRerun={() => void handleRerun()}
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
          <PlanRunKpiGrid
            devices={devicesQ.data}
            currentStage={timelineQ.data?.current_stage ?? null}
            patrolCycle={
              timelineQ.data?.stages?.find((s) => s.stage === 'patrol')
                ?.patrol_cycle_index ?? null
            }
          />
          <PlanChainSidebar
            chain={chainQ.data}
            isLoading={chainQ.isLoading}
            isError={chainQ.isError}
            chainDispatchFailed={chainDispatchFailed}
            onNavigateRun={(planRunId) => navigate(`/execution/plan-runs/${planRunId}`)}
          />
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

          <div className="flex-1 overflow-y-auto p-4 space-y-4">
            <DeviceOverview
              data={devicesQ.data}
              isLoading={devicesQ.isLoading}
              isError={devicesQ.isError}
              statusFilter={deviceStatusFilter}
              hostFilter={deviceHostFilter}
              onStatusFilterChange={setDeviceStatusFilter}
              onHostFilterChange={setDeviceHostFilter}
              onSelectDevice={(device) => setSelectedJobId(device.job_id)}
            />

            <AnomalyDashboard
              runId={id}
              data={watcherQ.data}
              isLoading={watcherQ.isLoading}
              isError={watcherQ.isError}
              timeScope={watcherTimeScope}
              onTimeScopeChange={setWatcherTimeScope}
            />

            <ArchiveStatusCard
              opsMetrics={watcherQ.data?.archive?.ops_metrics}
              scanStatus={watcherQ.data?.archive?.scan_status}
            />

            <DedupReportCard runId={id} />

            <BusinessFlowStepper
              timeline={timelineQ.data}
              isLoading={timelineQ.isLoading}
              isError={timelineQ.isError}
            />

            {(precheck || dispatchFailed) && (
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
            )}
          </div>
        </div>
      </div>

      <DeviceDetailDrawer
        device={selectedDevice}
        runId={id}
        onClose={() => setSelectedJobId(null)}
        onManualRetry={(jobId) => retryMut.mutate(jobId)}
        onManualExit={(jobId) => exitMut.mutate(jobId)}
        onOpenReport={(jobId) => navigate(`/runs/${jobId}/report`)}
        isRetryPending={retryMut.isPending}
        isExitPending={exitMut.isPending}
      />

      <AlertDialog open={finalArchiveOpen} onOpenChange={setFinalArchiveOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>PlanRun 已结束 — 是否最终归档？</AlertDialogTitle>
            <AlertDialogDescription>
              测试已中止或失败，系统不会自动归档。检测到已人工完成
              scan + merge，是否继续执行分类提取（按去重结果从 15.4
              取事件日志到提单目录）？
              {archiveReadiness?.reason ? ` 当前状态：${archiveReadiness.reason}` : ''}
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
    </div>
  );
}
