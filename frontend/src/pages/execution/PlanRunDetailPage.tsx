import { useCallback, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ArrowLeft, AlertCircle } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { useToast } from '@/components/ui/toast';
import { useSocketIO, type SocketIOMessage } from '@/hooks/useSocketIO';
import { api } from '@/utils/api';
import type {
  DeviceMatrixItem,
  DeviceUiStatus,
  EventSeverity,
  EventStage,
  PlanRunStatus,
} from '@/utils/api/types';
import PlanRunTopbar from '@/components/plan-run/PlanRunTopbar';
import PlanChainBreadcrumb from '@/components/plan-run/PlanChainBreadcrumb';
import DispatchGateCard from '@/components/plan-run/DispatchGateCard';
import BusinessFlowTimeline from '@/components/plan-run/BusinessFlowTimeline';
import DeviceMatrixCard from '@/components/plan-run/DeviceMatrixCard';
import DeviceDetailDrawer from '@/components/plan-run/DeviceDetailDrawer';
import WatcherSummaryCard from '@/components/plan-run/WatcherSummaryCard';

const TERMINAL: ReadonlyArray<PlanRunStatus> = [
  'SUCCESS',
  'PARTIAL_SUCCESS',
  'FAILED',
  'DEGRADED',
];

const FAST_REFETCH_MS = 5_000;
const SLOW_REFETCH_MS = 30_000;

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

  // ── Plan run + derived terminal flag drive every other refetch interval ──
  const runQ = useQuery({
    queryKey: ['plan-run', id],
    queryFn: () => api.planRuns.get(id),
    enabled: !!id,
    refetchInterval: (data) =>
      data && TERMINAL.includes(data.status) ? false : FAST_REFETCH_MS,
  });
  const isTerminal = !!runQ.data && TERMINAL.includes(runQ.data.status);
  const refetchInterval = isTerminal ? false : FAST_REFETCH_MS;

  const chainQ = useQuery({
    queryKey: ['plan-run-chain', id],
    queryFn: () => api.planRuns.getChain(id),
    enabled: !!id,
    refetchInterval: isTerminal ? false : SLOW_REFETCH_MS,
  });

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
    // Watcher window summaries are heavier — refresh half as often as the
    // device matrix.  watcher_signal SocketIO events still trigger immediate
    // invalidation below so we don't lose responsiveness.
    refetchInterval: isTerminal ? false : SLOW_REFETCH_MS,
  });

  // ── SocketIO event-driven invalidation ──
  // We deliberately do NOT mutate cached payloads inline — every push is
  // treated as an invalidation hint, and the next refetch resolves authoritative
  // state.  This matches the documented contract of the dashboard namespace.
  const onSocketMessage = useCallback(
    (msg: SocketIOMessage<unknown>) => {
      if (!id) return;
      if (msg.type === 'JOB_STATUS') {
        // A single device's status changed; refresh the per-device matrix +
        // timeline aggregations.  Keep the chain query alone to avoid noise.
        qc.invalidateQueries({ queryKey: ['plan-run-devices', id] });
        qc.invalidateQueries({ queryKey: ['plan-run-timeline', id] });
        qc.invalidateQueries({ queryKey: ['plan-run-events', id] });
      } else if (msg.type === 'PLAN_RUN_STATUS') {
        qc.invalidateQueries({ queryKey: ['plan-run', id] });
        qc.invalidateQueries({ queryKey: ['plan-run-chain', id] });
        qc.invalidateQueries({ queryKey: ['plan-run-timeline', id] });
        qc.invalidateQueries({ queryKey: ['plan-run-devices', id] });
      } else if (msg.type === 'WATCHER_SIGNAL') {
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

  // ── Loading + error states ──
  const planName = useMemo(
    () => timelineQ.data?.plan_name ?? null,
    [timelineQ.data?.plan_name],
  );

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

      {/* Topbar */}
      {runQ.isLoading ? (
        <Skeleton className="h-12 w-full rounded-xl" />
      ) : (
        <PlanRunTopbar
          run={runQ.data}
          planName={planName}
          isAborting={abortMut.isPending}
          onAbort={(reason) => abortMut.mutate(reason)}
          onExportReport={() =>
            toast.info('导出报告 — 功能开发中')
          }
        />
      )}

      {/* Plan chain */}
      <PlanChainBreadcrumb
        chain={chainQ.data}
        isLoading={chainQ.isLoading}
        onNavigateRun={(runIdToOpen) =>
          navigate(`/execution/plan-runs/${runIdToOpen}`)
        }
      />

      {/* Dispatch gate (precheck) — only renders when precheck context exists
          AND the gate is in-progress / failed / terminal-archived. */}
      <DispatchGateCard precheck={precheck} isTerminal={isTerminal} />

      {/* Business flow timeline */}
      <BusinessFlowTimeline
        timeline={timelineQ.data}
        events={eventsQ.data}
        stageFilter={stageFilter}
        severityFilter={severityFilter}
        onStageFilterChange={setStageFilter}
        onSeverityFilterChange={setSeverityFilter}
        isLoading={timelineQ.isLoading || eventsQ.isLoading}
      />

      {/* Device matrix */}
      <DeviceMatrixCard
        data={devicesQ.data}
        isLoading={devicesQ.isLoading}
        statusFilter={deviceStatusFilter}
        hostFilter={deviceHostFilter}
        onStatusFilterChange={setDeviceStatusFilter}
        onHostFilterChange={setDeviceHostFilter}
        onSelectDevice={setSelectedDevice}
      />

      {/* Watcher anomaly summary */}
      <WatcherSummaryCard
        data={watcherQ.data}
        isLoading={watcherQ.isLoading}
        windowMinutes={watcherWindow}
        onWindowChange={setWatcherWindow}
      />

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
