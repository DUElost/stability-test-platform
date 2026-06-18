import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ArrowLeft, AlertCircle, AlertTriangle, ChevronDown, RefreshCw, PanelLeft } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { useHeaderSlot } from '@/contexts/HeaderSlotContext';
import { Skeleton } from '@/components/ui/skeleton';
import { useToast } from '@/components/ui/toast';
import { useSocketIO, type SocketIOMessage } from '@/hooks/useSocketIO';
import { api } from '@/utils/api';
import { SOCKET_MESSAGE_TYPES } from '@/utils/socketEvents';
import type {
  DeviceMatrixItem,
  DeviceUiStatus,
  PlanRun,
  PlanRunStatus,
  WatcherTimeScope,
} from '@/utils/api/types';
import PlanRunHero from '@/components/plan-run/PlanRunHero';
import PlanRunKpiGrid from '@/components/plan-run/PlanRunKpiGrid';
import AnomalyDashboard from '@/components/plan-run/AnomalyDashboard';
import PlanChainSidebar from '@/components/plan-run/PlanChainSidebar';
import BusinessFlowStepper from '@/components/plan-run/BusinessFlowStepper';
import PlanRunTabs from '@/components/plan-run/PlanRunTabs';
import DeviceOverview from '@/components/plan-run/DeviceOverview';
import DeviceDetailDrawer from '@/components/plan-run/DeviceDetailDrawer';
import DispatchGateCard from '@/components/plan-run/DispatchGateCard';

import type { PrecheckState } from '@/utils/api/types';

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
const WATCHER_TIME_SCOPE_MAP: Record<string, WatcherTimeScope> = {
  all: 'all',
  '15m': '15m',
  '1h': '1h',
  '6h': '6h',
  '24h': '24h',
  '15': '15m',
  '60': '1h',
  '360': '6h',
  '1440': '24h',
};

function normalizeWatcherTimeScope(value: string | null): WatcherTimeScope {
  if (!value) return 'all';
  return WATCHER_TIME_SCOPE_MAP[value] ?? 'all';
}

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

/** Compact precheck summary row shown above the dispatch gate collapsible. */
function PrecheckSummaryRow({
  precheck,
  expanded,
  onToggle,
  gateFailed,
}: {
  precheck: PrecheckState;
  expanded: boolean;
  onToggle: () => void;
  gateFailed: boolean;
}) {
  const hosts = precheck.hosts ?? {};
  const hostEntries = Object.entries(hosts);
  const phase = precheck.phase;
  const mixedWatcherFailure =
    precheck.gate_failure?.code === 'MIXED_WATCHER_ACTIVITY'
      ? precheck.gate_failure
      : null;

  // Compute verified/total scripts across all hosts
  const { verified, total } = hostEntries.reduce(
    (acc, [, h]) => {
      const scripts = h.scripts ?? [];
      acc.total += scripts.length;
      acc.verified += scripts.filter((s) => s.ok).length;
      return acc;
    },
    { verified: 0, total: 0 },
  );

  const statusText =
    phase === 'ready'
      ? '通过'
      : phase === 'failed'
        ? '失败'
        : phase === 'syncing'
          ? '同步中'
          : phase === 'verifying' || phase === 'reverifying'
            ? '校验中'
            : phase;

  return (
    <button
      type="button"
      data-testid="precheck-row"
      onClick={onToggle}
      className="mx-1 flex w-full items-start gap-2 rounded-lg border border-gray-200 bg-white px-3 py-2 text-left shadow-sm hover:bg-gray-50"
    >
      <ChevronDown
        className={`mt-0.5 h-3.5 w-3.5 shrink-0 text-gray-400 transition-transform ${
          expanded ? '' : '-rotate-90'
        }`}
      />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="rounded bg-violet-100 px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wider text-violet-700">
            预检
          </span>
          <span className="flex-1 text-xs font-semibold text-gray-900">健康预检</span>
          <span
            className={`text-xs font-semibold ${
              phase === 'ready'
                ? 'text-green-600'
                : phase === 'failed'
                  ? 'text-red-600'
                  : 'text-amber-600'
            }`}
          >
            {statusText}
          </span>
          {gateFailed && <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-red-500" />}
        </div>
        <div className="mt-0.5 flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] text-gray-500">
          <span>
            <b className="font-semibold text-gray-800">{hostEntries.length}</b> 主机
          </span>
          {total > 0 && (
            <span>
              <b className="font-semibold text-gray-800">
                {verified}/{total}
              </b>{' '}
              脚本
            </span>
          )}
          {hostEntries.map(([hid]) => (
            <span key={hid} className="font-mono">{hid}</span>
          ))}
          {mixedWatcherFailure && (
            <span className="basis-full text-red-600">
              {mixedWatcherFailure.message}
            </span>
          )}
          {mixedWatcherFailure &&
            mixedWatcherFailure.inactive_host_ids.length > 0 && (
              <span className="basis-full font-mono text-red-500">
                不激活节点ID：{mixedWatcherFailure.inactive_host_ids.join(', ')}
              </span>
            )}
        </div>
      </div>
    </button>
  );
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
  const { setHeaderSlot, setFullBleed } = useHeaderSlot();

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

  const [selectedDevice, setSelectedDevice] = useState<DeviceMatrixItem | null>(null);
  const [diagOpen, setDiagOpen] = useState(false);
  const [leftPanelOpen, setLeftPanelOpen] = useState(false);

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
    queryKey: ['plan-run-watcher', id, watcherTimeScope],
    queryFn: () => api.planRuns.getWatcherSummary(id, watcherTimeScope),
    enabled: !!id,
    refetchInterval: isTerminal ? false : SLOW_REFETCH_MS,
  });

  const chainQ = useQuery({
    queryKey: ['plan-run-chain', id],
    queryFn: () => api.planRuns.getChain(id),
    enabled: !!id,
    refetchInterval: isTerminal ? false : refetchInterval,
  });

  const isAnyFetching =
    runQ.isFetching ||
    timelineQ.isFetching ||
    devicesQ.isFetching ||
    watcherQ.isFetching ||
    chainQ.isFetching;

  const refreshAll = useCallback(() => {
    (
      [
        'plan-run',
        'plan-run-timeline',
        'plan-run-devices',
        'plan-run-watcher',
        'plan-run-chain',
      ] as const
    ).forEach((k) => qc.invalidateQueries({ queryKey: [k, id] }));
  }, [qc, id]);

  // ── 将 "返回 / PlanRun # / tab / 最后更新 / 刷新" 注入 AppShell 顶栏 ──
  useEffect(() => {
    setFullBleed(true);
    setHeaderSlot(
      <div className="flex w-full items-center gap-3 min-w-0">
        <Button
          variant="ghost"
          size="sm"
          data-testid="plan-run-left-panel-toggle"
          onClick={() => setLeftPanelOpen((v) => !v)}
          aria-label="切换状态面板"
          className="-ml-1 px-1.5 text-gray-500 lg:hidden"
        >
          <PanelLeft className="h-4 w-4" />
        </Button>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => navigate('/execution/plan-runs')}
          className="-ml-2 text-xs text-gray-500"
        >
          <ArrowLeft className="mr-1 h-3.5 w-3.5" /> 返回执行列表
        </Button>
        <PlanRunTabs runId={id} active="overview" />
        <div className="ml-auto flex items-center gap-2">
          <span className="hidden text-[11px] text-gray-400 sm:inline">
            最后更新{' '}
            {runQ.dataUpdatedAt
              ? new Date(runQ.dataUpdatedAt).toLocaleTimeString('zh-CN')
              : '—'}
          </span>
          <Button
            variant="ghost"
            size="sm"
            data-testid="plan-run-refresh-btn"
            onClick={refreshAll}
            disabled={isAnyFetching}
            className="text-xs text-gray-500"
          >
            <RefreshCw
              className={`mr-1 h-3.5 w-3.5 ${isAnyFetching ? 'animate-spin' : ''}`}
            />
            刷新
          </Button>
        </div>
      </div>,
    );
    return () => {
      setHeaderSlot(null);
      setFullBleed(false);
    };
  }, [
    id,
    navigate,
    setHeaderSlot,
    setFullBleed,
    runQ.dataUpdatedAt,
    isAnyFetching,
    refreshAll,
  ]);

  const chainDispatchFailed = useMemo(() => {
    const summary = runQ.data?.result_summary;
    const fail = summary?.chain_dispatch_failed;
    if (fail && typeof fail === 'object' && 'error' in fail) {
      return fail as import('@/utils/api/types').ChainDispatchFailed;
    }
    return null;
  }, [runQ.data?.result_summary]);

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
      } else if (msg.type === SOCKET_MESSAGE_TYPES.PLAN_RUN_STATUS) {
        qc.invalidateQueries({ queryKey: ['plan-run', id] });
        qc.invalidateQueries({ queryKey: ['plan-run-chain', id] });
        qc.invalidateQueries({ queryKey: ['plan-run-timeline', id] });
        qc.invalidateQueries({ queryKey: ['plan-run-devices', id] });
      } else if (msg.type === SOCKET_MESSAGE_TYPES.PRECHECK_UPDATE) {
        qc.invalidateQueries({ queryKey: ['plan-run', id] });
        qc.invalidateQueries({ queryKey: ['plan-run-timeline', id] });
        qc.invalidateQueries({ queryKey: ['plan-run-devices', id] });
      } else if (msg.type === SOCKET_MESSAGE_TYPES.WATCHER_SIGNAL) {
        qc.invalidateQueries({ queryKey: ['plan-run-watcher', id] });
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
    <div className="flex h-full flex-col overflow-hidden bg-gray-50">
      {/* Main two-column layout */}
      <div className="flex flex-1 min-h-0 overflow-hidden">
        {/* 窄屏遮罩:点击关闭左栏抽屉 */}
        {leftPanelOpen && (
          <div
            data-testid="left-panel-backdrop"
            className="fixed inset-x-0 bottom-0 top-20 z-30 bg-black/30 lg:hidden"
            onClick={() => setLeftPanelOpen(false)}
          />
        )}
        {/* 左栏:宽屏固定双栏,窄屏左侧滑出抽屉 */}
        <aside
          className={`flex w-72 shrink-0 flex-col gap-4 overflow-y-auto border-r border-gray-100 bg-white p-4 transition-transform fixed bottom-0 left-0 top-20 z-40 shadow-xl lg:static lg:bottom-auto lg:top-auto lg:z-auto lg:shadow-none ${
            leftPanelOpen ? 'translate-x-0' : '-translate-x-full'
          } lg:translate-x-0`}
        >
          {runQ.isLoading ? (
            <Skeleton className="h-36 w-full rounded-xl" />
          ) : (
            <PlanRunHero
              run={runQ.data}
              planName={planName}
              isAborting={abortMut.isPending}
              onAbort={(reason) => abortMut.mutate(reason)}
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

        {/* Right panel — tabbed content area */}
        <div className="flex flex-1 flex-col min-h-0 overflow-hidden">
          {/* Stuck jobs banner */}
          {stuckJobs.length > 0 && (
            <div
              data-testid="stuck-jobs-banner"
              className="flex shrink-0 items-start gap-2 border-b border-amber-200 bg-amber-50 px-4 py-2.5 text-xs text-amber-900"
            >
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
              <div className="min-w-0 space-y-1">
                <p className="font-semibold">
                  {stuckJobs.length} 个 Job 心跳超时，可能已断开
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

          {/* 运行详情内容 */}
          <div className="flex-1 overflow-y-auto p-4 space-y-4">
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

            <AnomalyDashboard
              data={watcherQ.data}
              isLoading={watcherQ.isLoading}
              isError={watcherQ.isError}
              timeScope={watcherTimeScope}
              onTimeScopeChange={setWatcherTimeScope}
            />

            <BusinessFlowStepper
              timeline={timelineQ.data}
              isLoading={timelineQ.isLoading}
              isError={timelineQ.isError}
            />

            {/* 派发门禁诊断(折叠; 失败默认展开) */}
            {precheck && (
              <section data-testid="dispatch-gate-section" className="space-y-2">
                <PrecheckSummaryRow
                  precheck={precheck}
                  expanded={showDiag}
                  onToggle={() => setDiagOpen((v) => !v)}
                  gateFailed={gateFailed}
                />
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
          </div>
        </div>
      </div>

      {/* Device detail drawer (floating overlay) */}
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
