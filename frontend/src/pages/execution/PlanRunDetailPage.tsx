import { useCallback, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ArrowLeft, AlertCircle, AlertTriangle, ChevronDown } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { useToast } from '@/components/ui/toast';
import { useSocketIO, type SocketIOMessage } from '@/hooks/useSocketIO';
import { api } from '@/utils/api';
import { SOCKET_MESSAGE_TYPES } from '@/utils/socketEvents';
import type {
  DeviceMatrixItem,
  DeviceUiStatus,
  EventSeverity,
  EventStage,
  PlanRun,
  PlanRunStatus,
} from '@/utils/api/types';
import PlanRunHero from '@/components/plan-run/PlanRunHero';
import PlanChainBreadcrumb from '@/components/plan-run/PlanChainBreadcrumb';
import BusinessFlowTimeline from '@/components/plan-run/BusinessFlowTimeline';
import DeviceOverview from '@/components/plan-run/DeviceOverview';
import DeviceDetailDrawer from '@/components/plan-run/DeviceDetailDrawer';
import WatcherSummaryCard from '@/components/plan-run/WatcherSummaryCard';
import DispatchGateCard from '@/components/plan-run/DispatchGateCard';
import PlanRunKpiBar from '@/components/plan-run/PlanRunKpiBar';

const TERMINAL: ReadonlyArray<PlanRunStatus> = [
  'SUCCESS',
  'PARTIAL_SUCCESS',
  'FAILED',
  'DEGRADED',
];

const GATE_ACTIVE_REFETCH_MS = 3_000;
const FAST_REFETCH_MS = 10_000;
const SLOW_REFETCH_MS = 30_000;

/** Patrol heartbeat stale threshold — matches backend _LIVE_PATROL_HEARTBEAT_WINDOW (180s). */
const STALE_PATROL_HEARTBEAT_MS = 180_000;
/** Init-stage RUNNING without patrol heartbeat — matches RUNNING_HEARTBEAT_TIMEOUT (900s). */
const STALE_INIT_HEARTBEAT_MS = 900_000;

function isDispatchGateActive(run: PlanRun | undefined): boolean {
  if (!run || run.status !== 'RUNNING') return false;

  const precheck = run.run_context?.precheck;
  const dispatch = run.run_context?.dispatch_state;

  if (!precheck) {
    return dispatch?.status === 'queued' || dispatch?.status === 'running';
  }

  if (precheck.phase !== 'ready' && precheck.phase !== 'failed') {
    return true;
  }

  if (precheck.phase === 'ready') {
    const dispatchStatus = dispatch?.status;
    return dispatchStatus !== 'completed' && dispatchStatus !== 'failed';
  }

  return false;
}

function isJobStuck(d: DeviceMatrixItem, now = Date.now()): boolean {
  if (d.job_status !== 'RUNNING') return false;
  if (d.last_heartbeat_at) {
    const t = new Date(d.last_heartbeat_at).getTime();
    if (!Number.isNaN(t) && now - t > STALE_PATROL_HEARTBEAT_MS) return true;
  }
  if (d.current_stage === 'patrol') return false;
  if (d.started_at) {
    const t = new Date(d.started_at).getTime();
    if (!Number.isNaN(t) && now - t > STALE_INIT_HEARTBEAT_MS) return true;
  }
  return false;
}

export default function PlanRunDetailPage() {
  const { runId } = useParams<{ runId: string }>();
  const id = Number(runId);
  const navigate = useNavigate();
  const qc = useQueryClient();
  const toast = useToast();

  const [stageFilter, setStageFilter] = useState<EventStage | 'all'>('all');
  const [severityFilter, setSeverityFilter] = useState<EventSeverity | 'all'>('all');
  const [deviceStatusFilter, setDeviceStatusFilter] = useState<
    DeviceUiStatus | 'all'
  >('all');
  const [deviceHostFilter, setDeviceHostFilter] = useState<string | 'all'>('all');
  const [watcherWindow, setWatcherWindow] = useState<number>(60);
  const [selectedDevice, setSelectedDevice] = useState<DeviceMatrixItem | null>(null);
  const [diagOpen, setDiagOpen] = useState(false);

  // ── Plan run + derived terminal flag drive every other refetch interval ──
  const runQ = useQuery({
    queryKey: ['plan-run', id],
    queryFn: () => api.planRuns.get(id),
    enabled: !!id,
    refetchInterval: (data) => {
      if (data && TERMINAL.includes(data.status)) return false;
      return isDispatchGateActive(data) ? GATE_ACTIVE_REFETCH_MS : FAST_REFETCH_MS;
    },
  });
  const isTerminal = !!runQ.data && TERMINAL.includes(runQ.data.status);
  const gateActive = isDispatchGateActive(runQ.data);
  const refetchInterval = isTerminal
    ? false
    : gateActive
      ? GATE_ACTIVE_REFETCH_MS
      : FAST_REFETCH_MS;

  const timelineQ = useQuery({
    queryKey: ['plan-run-timeline', id],
    queryFn: () => api.planRuns.getTimeline(id),
    enabled: !!id,
    refetchInterval,
  });

  const eventsQ = useQuery({
    queryKey: ['plan-run-events', id, stageFilter, severityFilter],
    queryFn: () =>
      api.planRuns.getEvents(id, {
        stage: stageFilter,
        severity: severityFilter,
        limit: 100,
      }),
    enabled: !!id,
    refetchInterval,
  });

  const devicesQ = useQuery({
    queryKey: ['plan-run-devices', id, deviceStatusFilter, deviceHostFilter],
    queryFn: () =>
      api.planRuns.getDevices(id, {
        status: deviceStatusFilter,
        host_id: deviceHostFilter,
      }),
    enabled: !!id,
    refetchInterval,
  });

  const watcherQ = useQuery({
    queryKey: ['plan-run-watcher', id, watcherWindow],
    queryFn: () => api.planRuns.getWatcherSummary(id, watcherWindow),
    enabled: !!id,
    refetchInterval: isTerminal ? false : SLOW_REFETCH_MS,
  });

  const chainQ = useQuery({
    queryKey: ['plan-run-chain', id],
    queryFn: () => api.planRuns.getChain(id),
    enabled: !!id,
    refetchInterval: isTerminal ? false : refetchInterval,
  });

  const chainDispatchFailed = useMemo(() => {
    const summary = runQ.data?.result_summary;
    const fail = summary?.chain_dispatch_failed;
    if (fail && typeof fail === 'object' && 'error' in fail) {
      return fail;
    }
    return null;
  }, [runQ.data?.result_summary]);

  const showPlanChain =
    chainDispatchFailed != null
    || (chainQ.data?.nodes?.length ?? 0) > 1
    || chainQ.isLoading;

  const stuckJobs = useMemo(() => {
    if (isTerminal || !devicesQ.data?.devices?.length) return [];
    const now = Date.now();
    return devicesQ.data.devices.filter((d) => isJobStuck(d, now));
  }, [devicesQ.data, isTerminal]);

  // ── SocketIO event-driven invalidation ──
  const onSocketMessage = useCallback(
    (msg: SocketIOMessage<unknown>) => {
      if (!id) return;
      if (msg.type === SOCKET_MESSAGE_TYPES.JOB_STATUS) {
        qc.invalidateQueries({ queryKey: ['plan-run-devices', id] });
        qc.invalidateQueries({ queryKey: ['plan-run-timeline', id] });
        qc.invalidateQueries({ queryKey: ['plan-run-events', id] });
      } else if (msg.type === SOCKET_MESSAGE_TYPES.PLAN_RUN_STATUS) {
        qc.invalidateQueries({ queryKey: ['plan-run', id] });
        qc.invalidateQueries({ queryKey: ['plan-run-chain', id] });
        qc.invalidateQueries({ queryKey: ['plan-run-timeline', id] });
        qc.invalidateQueries({ queryKey: ['plan-run-devices', id] });
      } else if (msg.type === SOCKET_MESSAGE_TYPES.PRECHECK_UPDATE) {
        qc.invalidateQueries({ queryKey: ['plan-run', id] });
        qc.invalidateQueries({ queryKey: ['plan-run-timeline', id] });
        qc.invalidateQueries({ queryKey: ['plan-run-events', id] });
        qc.invalidateQueries({ queryKey: ['plan-run-devices', id] });
      } else if (msg.type === SOCKET_MESSAGE_TYPES.WATCHER_SIGNAL) {
        qc.invalidateQueries({ queryKey: ['plan-run-watcher', id] });
        qc.invalidateQueries({ queryKey: ['plan-run-events', id] });
      }
    },
    [id, qc],
  );

  useSocketIO(id ? `/ws/plan-runs/${id}` : '', {
    enabled: !!id && !isTerminal,
    onMessage: onSocketMessage,
  });

  // ── Mutations: abort PlanRun + manual retry/exit ──
  const abortMut = useMutation({
    mutationFn: (reason: string) => api.planRuns.abort(id, reason),
    onSuccess: (data) => {
      toast.success(`PlanRun 中止已发起 — 状态: ${data.status}`);
      qc.invalidateQueries({ queryKey: ['plan-run', id] });
      qc.invalidateQueries({ queryKey: ['plan-run-timeline', id] });
      qc.invalidateQueries({ queryKey: ['plan-run-events', id] });
      qc.invalidateQueries({ queryKey: ['plan-run-devices', id] });
    },
    onError: (err: unknown) => {
      const msg = err instanceof Error ? err.message : String(err);
      toast.error(`中止失败: ${msg}`);
    },
  });

  const retryMut = useMutation({
    mutationFn: (jobId: number) => api.planRuns.manualRetryJob(id, jobId),
    onSuccess: (data) => {
      toast.success(`已请求 Job #${data.job_id} 立即重试`);
      qc.invalidateQueries({ queryKey: ['plan-run-devices', id] });
    },
    onError: (err: unknown) => {
      const msg = err instanceof Error ? err.message : String(err);
      toast.error(`重试失败: ${msg}`);
    },
  });

  const exitMut = useMutation({
    mutationFn: (jobId: number) => api.planRuns.manualExitJob(id, jobId),
    onSuccess: (data) => {
      toast.success(`已请求 Job #${data.job_id} 退出`);
      qc.invalidateQueries({ queryKey: ['plan-run-devices', id] });
    },
    onError: (err: unknown) => {
      const msg = err instanceof Error ? err.message : String(err);
      toast.error(`退出失败: ${msg}`);
    },
  });

  const retryDispatchMut = useMutation({
    mutationFn: () => api.planRuns.retryDispatch(id),
    onSuccess: () => {
      toast.success('已重新入队派发门禁');
      qc.invalidateQueries({ queryKey: ['plan-run', id] });
      qc.invalidateQueries({ queryKey: ['plan-run-timeline', id] });
      qc.invalidateQueries({ queryKey: ['plan-run-events', id] });
    },
    onError: (err: unknown) => {
      const msg = err instanceof Error ? err.message : String(err);
      toast.error(`重试派发失败: ${msg}`);
    },
  });

  // ── Plan name ──
  const planName = useMemo(
    () => timelineQ.data?.plan_name ?? null,
    [timelineQ.data?.plan_name],
  );

  // ── Error / invalid states ──
  if (!id || Number.isNaN(id)) {
    return (
      <div className="flex h-64 items-center justify-center text-sm text-gray-500">
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
        <div className="flex h-48 items-center justify-center rounded-lg border bg-red-50 text-sm text-red-700">
          <AlertCircle className="mr-2 h-4 w-4" />
          {(runQ.error as Error)?.message || '加载 PlanRun 失败'}
        </div>
      </div>
    );
  }

  const precheck = runQ.data?.run_context?.precheck ?? null;
  const dispatchState = runQ.data?.run_context?.dispatch_state ?? null;
  const gateFailed =
    precheck?.phase === 'failed' || dispatchState?.status === 'failed';
  const showDiag = diagOpen || gateFailed;

  return (
    <div className="mx-auto max-w-[1480px] space-y-3 px-1 pb-12">
      {/* Back link */}
      <div className="flex items-center">
        <Button
          variant="ghost"
          size="sm"
          onClick={() => navigate('/execution/plan-runs')}
          className="-ml-2 text-xs text-gray-500"
        >
          <ArrowLeft className="mr-1 h-3.5 w-3.5" /> 返回执行列表
        </Button>
      </div>

      {/* Hero / summary card */}
      {runQ.isLoading ? (
        <Skeleton className="h-20 w-full rounded-xl" />
      ) : (
        <PlanRunHero
          run={runQ.data}
          planName={planName}
          isAborting={abortMut.isPending}
          onAbort={(reason) => abortMut.mutate(reason)}
          onExportReport={async () => {
            try {
              const blob = await api.planRuns.exportReport(id, 'markdown');
              const url = URL.createObjectURL(blob);
              const anchor = document.createElement('a');
              anchor.href = url;
              anchor.download = `plan-run-${id}-report.md`;
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

      {/* KPI 态势条 */}
      <PlanRunKpiBar
        devices={devicesQ.data}
        currentStage={timelineQ.data?.current_stage ?? null}
        patrolCycle={
          timelineQ.data?.stages?.find((s) => s.stage === 'patrol')
            ?.patrol_cycle_index ?? null
        }
      />

      {stuckJobs.length > 0 && (
        <div
          data-testid="stuck-jobs-banner"
          className="flex items-start gap-2 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2.5 text-xs text-amber-900"
        >
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
          <div className="min-w-0 space-y-1">
            <p className="font-semibold">
              {stuckJobs.length} 个 Job 心跳超时，可能已失联
            </p>
            <p className="text-xs text-amber-800/90">
              后端 recycler 将把超时 Job 标记为 UNKNOWN；grace 窗口内 Agent 可通过 recovery 恢复。
              设备：
              {stuckJobs
                .map((d) => d.device_serial || `#${d.device_id}`)
                .join('、')}
            </p>
          </div>
        </div>
      )}

      {/* 设备总览(全宽; minimap 方块按数量自适应铺满,空间随设备数增加) */}
      <DeviceOverview
        data={devicesQ.data}
        isLoading={devicesQ.isLoading}
        isError={devicesQ.isError}
        statusFilter={deviceStatusFilter}
        hostFilter={deviceHostFilter}
        onStatusFilterChange={setDeviceStatusFilter}
        onHostFilterChange={setDeviceHostFilter}
        onSelectDevice={setSelectedDevice}
      />

      {/* Watcher 异常聚合(全宽,始终在设备总览下方) */}
      <WatcherSummaryCard
        data={watcherQ.data}
        isLoading={watcherQ.isLoading}
        isError={watcherQ.isError}
        windowMinutes={watcherWindow}
        onWindowChange={setWatcherWindow}
      />

      {showPlanChain && (
        <PlanChainBreadcrumb
          chain={chainQ.data}
          isLoading={chainQ.isLoading}
          isError={chainQ.isError}
          chainDispatchFailed={chainDispatchFailed}
          onNavigateRun={(planRunId) => navigate(`/execution/plan-runs/${planRunId}`)}
        />
      )}

      {/* Business flow timeline (全宽) */}
      <BusinessFlowTimeline
        timeline={timelineQ.data}
        events={eventsQ.data}
        stageFilter={stageFilter}
        severityFilter={severityFilter}
        onStageFilterChange={setStageFilter}
        onSeverityFilterChange={setSeverityFilter}
        isLoading={timelineQ.isLoading || eventsQ.isLoading}
        isError={timelineQ.isError || eventsQ.isError}
        precheck={precheck}
        dispatchState={dispatchState}
      />

      {/* 派发门禁诊断(折叠; 失败默认展开; 折叠态保持 mount 以便审计/测试) */}
      {precheck && (
        <section data-testid="dispatch-gate-section" className="space-y-2">
          <button
            type="button"
            data-testid="dispatch-gate-section-toggle"
            onClick={() => setDiagOpen((v) => !v)}
            className="mx-1 flex items-center gap-2 text-left"
          >
            <ChevronDown
              className={`h-3.5 w-3.5 text-gray-400 transition-transform ${
                showDiag ? '' : '-rotate-90'
              }`}
            />
            <span className="text-xs font-bold uppercase tracking-wider text-gray-700">
              派发门禁诊断
            </span>
            <span className="text-xs text-gray-400">
              {gateFailed ? '失败 · 已展开' : showDiag ? '点击收起' : '点击展开'}
            </span>
            {gateFailed && <span className="h-1.5 w-1.5 rounded-full bg-red-500" />}
          </button>
          <div className={showDiag ? '' : 'hidden'}>
            <DispatchGateCard
              precheck={precheck}
              dispatchState={dispatchState}
              isTerminal={isTerminal}
              onRetryDispatch={() => retryDispatchMut.mutate()}
              isRetrying={retryDispatchMut.isPending}
            />
          </div>
        </section>
      )}

      {/* Device detail drawer */}
      <DeviceDetailDrawer
        device={selectedDevice}
        onClose={() => setSelectedDevice(null)}
        onManualRetry={(jobId) => retryMut.mutate(jobId)}
        onManualExit={(jobId) => exitMut.mutate(jobId)}
        onOpenReport={(jobId) => navigate(`/runs/${jobId}/report`)}
        isRetryPending={retryMut.isPending}
        isExitPending={exitMut.isPending}
      />
    </div>
  );
}
