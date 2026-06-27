import { useCallback, useMemo, useRef } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useToast } from '@/hooks/useToast';
import { useSocketIO, type SocketIOMessage } from '@/hooks/useSocketIO';
import {
  isJobStuck,
  isPlanRunTerminal,
  planRunRefetchInterval,
  SLOW_REFETCH_MS,
} from '@/hooks/plan-run/planRunDetailUtils';
import { api } from '@/utils/api';
import { dedupKeys, planRunKeys } from '@/utils/api/queryKeys';
import { SOCKET_MESSAGE_TYPES } from '@/utils/socketEvents';
import type { ChainDispatchFailed, DeviceUiStatus, WatcherTimeScope } from '@/utils/api/types';

interface Filters {
  deviceStatusFilter: DeviceUiStatus | 'all';
  deviceHostFilter: string;
  watcherTimeScope: WatcherTimeScope;
}

export function usePlanRunDetailData(id: number, filters: Filters) {
  const { deviceStatusFilter, deviceHostFilter, watcherTimeScope } = filters;
  const qc = useQueryClient();
  const toast = useToast();
  const watcherSignalTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const runQ = useQuery({
    queryKey: planRunKeys.detail(id),
    queryFn: () => api.planRuns.get(id),
    enabled: !!id,
    refetchInterval: (data) => planRunRefetchInterval(data, isPlanRunTerminal(data?.status)),
  });

  const isTerminal = isPlanRunTerminal(runQ.data?.status);
  const refetchInterval = planRunRefetchInterval(runQ.data, isTerminal);

  const timelineQ = useQuery({
    queryKey: planRunKeys.timeline(id),
    queryFn: () => api.planRuns.getTimeline(id),
    enabled: !!id,
    refetchInterval,
  });

  const devicesQ = useQuery({
    queryKey: planRunKeys.devices(id, deviceStatusFilter, deviceHostFilter),
    queryFn: () =>
      api.planRuns.getDevices(id, {
        status: deviceStatusFilter,
        host_id: deviceHostFilter,
      }),
    enabled: !!id,
    refetchInterval,
  });

  const watcherQ = useQuery({
    queryKey: planRunKeys.watcher(id, watcherTimeScope),
    queryFn: () => api.planRuns.getWatcherSummary(id, watcherTimeScope),
    enabled: !!id,
    refetchInterval: isTerminal ? false : SLOW_REFETCH_MS,
  });

  const chainQ = useQuery({
    queryKey: planRunKeys.chain(id),
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
    qc.invalidateQueries({ queryKey: planRunKeys.detail(id) });
    qc.invalidateQueries({ queryKey: planRunKeys.timeline(id) });
    qc.invalidateQueries({ queryKey: planRunKeys.devicesByRun(id) });
    qc.invalidateQueries({ queryKey: planRunKeys.watcherByRun(id) });
    qc.invalidateQueries({ queryKey: planRunKeys.chain(id) });
  }, [qc, id]);

  const onSocketMessage = useCallback(
    (msg: SocketIOMessage<unknown>) => {
      if (!id) return;
      if (msg.type === SOCKET_MESSAGE_TYPES.JOB_STATUS) {
        qc.invalidateQueries({ queryKey: planRunKeys.devicesByRun(id) });
        qc.invalidateQueries({ queryKey: planRunKeys.timeline(id) });
      } else if (msg.type === SOCKET_MESSAGE_TYPES.PLAN_RUN_STATUS) {
        qc.invalidateQueries({ queryKey: planRunKeys.detail(id) });
        qc.invalidateQueries({ queryKey: planRunKeys.chain(id) });
        qc.invalidateQueries({ queryKey: planRunKeys.timeline(id) });
        qc.invalidateQueries({ queryKey: planRunKeys.devicesByRun(id) });
      } else if (msg.type === SOCKET_MESSAGE_TYPES.PRECHECK_UPDATE) {
        qc.invalidateQueries({ queryKey: planRunKeys.detail(id) });
        qc.invalidateQueries({ queryKey: planRunKeys.timeline(id) });
        qc.invalidateQueries({ queryKey: planRunKeys.devicesByRun(id) });
      } else if (msg.type === SOCKET_MESSAGE_TYPES.WATCHER_SIGNAL) {
        if (watcherSignalTimer.current) clearTimeout(watcherSignalTimer.current);
        watcherSignalTimer.current = setTimeout(() => {
          qc.invalidateQueries({ queryKey: planRunKeys.watcherByRun(id) });
          watcherSignalTimer.current = null;
        }, 2000);
      }
    },
    [id, qc],
  );

  useSocketIO(id ? `/ws/plan-runs/${id}` : '', {
    enabled: !!id && !isTerminal,
    onMessage: onSocketMessage,
  });

  const abortMut = useMutation({
    mutationFn: (reason: string) => api.planRuns.abort(id, reason),
    onSuccess: (data) => {
      toast.success(`PlanRun 中止已发起 — 状态: ${data.status}`);
      qc.invalidateQueries({ queryKey: planRunKeys.detail(id) });
      qc.invalidateQueries({ queryKey: planRunKeys.timeline(id) });
      qc.invalidateQueries({ queryKey: planRunKeys.events(id) });
      qc.invalidateQueries({ queryKey: planRunKeys.devicesByRun(id) });
    },
    onError: (err: unknown) => {
      const msg = err instanceof Error ? err.message : String(err);
      toast.error(`中止失败: ${msg}`);
    },
  });

  const finalArchiveMut = useMutation({
    mutationFn: () => api.planRuns.triggerExtract(id),
    onSuccess: (data) => {
      toast.success(`已提取 ${data.extracted_count} 个事件目录到提单目录`);
      qc.invalidateQueries({ queryKey: dedupKeys.status(id) });
    },
    onError: (err: unknown) => {
      const msg = err instanceof Error ? err.message : String(err);
      toast.error(`最终归档失败: ${msg}`);
    },
  });

  const retryMut = useMutation({
    mutationFn: (jobId: number) => api.planRuns.manualRetryJob(id, jobId),
    onSuccess: (data) => {
      toast.success(`已请求 Job #${data.job_id} 立即重试`);
      qc.invalidateQueries({ queryKey: planRunKeys.devicesByRun(id) });
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
      qc.invalidateQueries({ queryKey: planRunKeys.devicesByRun(id) });
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
      qc.invalidateQueries({ queryKey: planRunKeys.detail(id) });
      qc.invalidateQueries({ queryKey: planRunKeys.timeline(id) });
      qc.invalidateQueries({ queryKey: planRunKeys.events(id) });
    },
    onError: (err: unknown) => {
      const msg = err instanceof Error ? err.message : String(err);
      toast.error(`重试派发失败: ${msg}`);
    },
  });

  const chainDispatchFailed = useMemo(() => {
    const summary = runQ.data?.result_summary;
    const fail = summary?.chain_dispatch_failed;
    if (fail && typeof fail === 'object' && 'error' in fail) {
      return fail as ChainDispatchFailed;
    }
    return null;
  }, [runQ.data?.result_summary]);

  const stuckJobs = useMemo(() => {
    if (isTerminal || !devicesQ.data?.devices?.length) return [];
    const now = Date.now();
    return devicesQ.data.devices.filter((d) => isJobStuck(d, now));
  }, [devicesQ.data, isTerminal]);

  const planName = useMemo(
    () => timelineQ.data?.plan_name ?? null,
    [timelineQ.data?.plan_name],
  );

  return {
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
  };
}
