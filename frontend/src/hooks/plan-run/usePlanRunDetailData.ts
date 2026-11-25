import { useCallback, useEffect, useMemo, useRef } from 'react';
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useToast } from '@/hooks/useToast';
import { useSocketIO, type SocketIOMessage } from '@/hooks/useSocketIO';
import { planRunSubscription } from '@/config';
import {
  isPlanRunTerminal,
  PLAN_RUN_SOCKET_COALESCE_MS,
  planRunJobStatusInvalidateKeys,
  planRunPrecheckInvalidateKeys,
  planRunRefetchInterval,
  planRunRefreshKeys,
  SLOW_REFETCH_MS,
} from '@/hooks/plan-run/planRunDetailUtils';
import { api } from '@/utils/api';
import { dedupKeys, planRunKeys } from '@/utils/api/queryKeys';
import { SOCKET_MESSAGE_TYPES } from '@/utils/socketEvents';
import type {
  ChainDispatchFailed,
  PlanRunMinimapDevice,
  WatcherTimeScope,
} from '@/utils/api/types';
import type {
  PlanRunDeviceLinkFilter,
  PlanRunDeviceStatusFilter,
} from '@/components/plan-run/PlanRunKpiGrid';

interface Filters {
  deviceStatusFilter: PlanRunDeviceStatusFilter;
  deviceLinkFilter: PlanRunDeviceLinkFilter;
  deviceHostFilter: string;
  deviceSearchQuery?: string;
  viewMode?: 'grid' | 'table';
  devicePage?: number;
  devicePageSize?: number;
  selectedJobId?: number | null;
  watcherTimeScope: WatcherTimeScope;
  /** #3653 U4：快照抽屉选中的 Job，用于读取 L3 投影；null = 只看 L2 */
  projectionJobId?: number | null;
}

export function usePlanRunDetailData(id: number, filters: Filters) {
  const {
    deviceStatusFilter,
    deviceLinkFilter,
    deviceHostFilter,
    deviceSearchQuery = '',
    viewMode = 'grid',
    devicePage = 1,
    devicePageSize = 50,
    selectedJobId = null,
    watcherTimeScope,
    projectionJobId = null,
  } = filters;
  const qc = useQueryClient();
  const toast = useToast();
  const watcherSignalTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const jobStatusTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const precheckTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const selectedJobIdRef = useRef<number | null>(selectedJobId);
  useEffect(() => {
    selectedJobIdRef.current = selectedJobId;
  }, [selectedJobId]);

  useEffect(() => {
    return () => {
      if (watcherSignalTimer.current) clearTimeout(watcherSignalTimer.current);
      if (jobStatusTimer.current) clearTimeout(jobStatusTimer.current);
      if (precheckTimer.current) clearTimeout(precheckTimer.current);
    };
  }, []);

  const runQ = useQuery({
    queryKey: planRunKeys.detail(id),
    // #2623：本页不消费内嵌 jobs（占响应 98.9%）——opt-out 拿轻量响应
    queryFn: () => api.planRuns.get(id, { includeJobs: false }),
    enabled: !!id,
    refetchInterval: (query) =>
      planRunRefetchInterval(
        query.state.data,
        isPlanRunTerminal(query.state.data?.status),
      ),
  });

  const isTerminal = isPlanRunTerminal(runQ.data?.status);
  const refetchInterval = planRunRefetchInterval(runQ.data, isTerminal);

  const timelineQ = useQuery({
    queryKey: planRunKeys.timeline(id),
    queryFn: () => api.planRuns.getTimeline(id),
    enabled: !!id,
    refetchInterval,
  });

  // B3-3（契约 §5.1）：独立轻量全集 Minimap（7 字段设备格子 + 全局 facets/hosts/stuck_job_ids）
  // 任何情况下不截断为单页，且驱动超时横幅、Watcher 串号反查与快照设备选择器。
  const minimapQ = useQuery({
    queryKey: planRunKeys.devicesMinimap(id),
    queryFn: () => api.planRuns.getDevicesMinimap(id),
    enabled: !!id,
    refetchInterval,
  });

  // B3-3（契约 §5.2）：仅在表格视图按需拉取当前页（默认 limit=50，支持真实并集筛选与串号搜索）
  const tablePageParams = useMemo(
    () => ({
      status: deviceStatusFilter,
      link_status: deviceLinkFilter,
      host_id: deviceHostFilter,
      ...(deviceSearchQuery.trim() ? { q: deviceSearchQuery.trim() } : {}),
      skip: (Math.max(1, devicePage) - 1) * devicePageSize,
      limit: devicePageSize,
    }),
    [deviceStatusFilter, deviceLinkFilter, deviceHostFilter, deviceSearchQuery, devicePage, devicePageSize],
  );

  const devicesPageQ = useQuery({
    queryKey: planRunKeys.devicesPage(id, tablePageParams),
    queryFn: () => api.planRuns.getDevices(id, tablePageParams),
    enabled: !!id && viewMode === 'table',
    placeholderData: keepPreviousData,
    refetchInterval: viewMode === 'table' ? refetchInterval : false,
  });

  // B3-3（契约 §5.3）：单 Job 完整详情按 (runId, jobId) 严格隔离加载
  const selectedJobQ = useQuery({
    queryKey: planRunKeys.jobDetail(id, selectedJobId ?? 0),
    queryFn: () => api.planRuns.getJobDetail(id, selectedJobId as number),
    enabled: !!id && selectedJobId != null,
    refetchInterval: selectedJobId != null ? refetchInterval : false,
  });

  const watcherQ = useQuery({
    queryKey: planRunKeys.watcher(id, watcherTimeScope),
    queryFn: () => api.planRuns.getWatcherSummary(id, watcherTimeScope),
    enabled: !!id,
    refetchInterval: isTerminal ? false : SLOW_REFETCH_MS,
  });

  // #529：终态 DLE 事件视图由 LogEventsCard 自持查询（同 key），此处不再重复声明。
  const chainQ = useQuery({
    queryKey: planRunKeys.chain(id),
    queryFn: () => api.planRuns.getChain(id),
    enabled: !!id,
    refetchInterval: isTerminal ? false : refetchInterval,
  });

  // #3653 U4：按具体 Job 读 L3；键含 jobId，切换设备不会串缓存值。
  const jobProjectionQ = useQuery({
    queryKey: planRunKeys.jobParameterProjection(id, projectionJobId ?? 0),
    queryFn: () => api.planRuns.jobParameterProjection(id, projectionJobId as number),
    enabled: !!id && projectionJobId != null,
  });

  const isAnyFetching =
    runQ.isFetching ||
    timelineQ.isFetching ||
    minimapQ.isFetching ||
    devicesPageQ.isFetching ||
    selectedJobQ.isFetching ||
    watcherQ.isFetching ||
    chainQ.isFetching ||
    jobProjectionQ.isFetching;

  const invalidateSelectedJob = useCallback(() => {
    const activeJobId = selectedJobIdRef.current;
    if (activeJobId != null) {
      qc.invalidateQueries({ queryKey: planRunKeys.jobDetail(id, activeJobId) });
    }
  }, [qc, id]);

  const refreshAll = useCallback(() => {
    // #1193：手动刷新覆盖去重状态与逐条用例结果等后处理产物（键表见 utils）。
    for (const key of planRunRefreshKeys(id)) {
      qc.invalidateQueries({ queryKey: key });
    }
    invalidateSelectedJob();
  }, [qc, id, invalidateSelectedJob]);

  const onSocketMessage = useCallback(
    (msg: SocketIOMessage<unknown>) => {
      if (!id) return;
      if (msg.type === SOCKET_MESSAGE_TYPES.JOB_STATUS) {
        // #2369：密集 JOB_STATUS 合流，避免 devices/timeline/logs 三联 REST 风暴。
        if (jobStatusTimer.current) clearTimeout(jobStatusTimer.current);
        jobStatusTimer.current = setTimeout(() => {
          for (const key of planRunJobStatusInvalidateKeys(id)) {
            qc.invalidateQueries({ queryKey: key });
          }
          invalidateSelectedJob();
          jobStatusTimer.current = null;
        }, PLAN_RUN_SOCKET_COALESCE_MS);
      } else if (msg.type === SOCKET_MESSAGE_TYPES.PLAN_RUN_STATUS) {
        // Run 级状态变迁低频，保持即时（abort/终态需尽快反映）。
        qc.invalidateQueries({ queryKey: planRunKeys.detail(id) });
        qc.invalidateQueries({ queryKey: planRunKeys.chain(id) });
        qc.invalidateQueries({ queryKey: planRunKeys.timeline(id) });
        qc.invalidateQueries({ queryKey: planRunKeys.devicesByRun(id) });
        qc.invalidateQueries({ queryKey: planRunKeys.logsByRun(id) });
        invalidateSelectedJob();
      } else if (msg.type === SOCKET_MESSAGE_TYPES.PRECHECK_UPDATE) {
        if (precheckTimer.current) clearTimeout(precheckTimer.current);
        precheckTimer.current = setTimeout(() => {
          for (const key of planRunPrecheckInvalidateKeys(id)) {
            qc.invalidateQueries({ queryKey: key });
          }
          precheckTimer.current = null;
        }, PLAN_RUN_SOCKET_COALESCE_MS);
      } else if (msg.type === SOCKET_MESSAGE_TYPES.WATCHER_SIGNAL) {
        if (watcherSignalTimer.current) clearTimeout(watcherSignalTimer.current);
        watcherSignalTimer.current = setTimeout(() => {
          qc.invalidateQueries({ queryKey: planRunKeys.watcherByRun(id) });
          watcherSignalTimer.current = null;
        }, PLAN_RUN_SOCKET_COALESCE_MS);
      }
    },
    [id, qc, invalidateSelectedJob],
  );

  useSocketIO(id ? planRunSubscription(id) : '', {
    enabled: !!id && !isTerminal,
    onMessage: onSocketMessage,
  });

  const abortMut = useMutation({
    mutationFn: (reason: string) => api.planRuns.abort(id, reason),
    onSuccess: (data) => {
      toast.success(`PlanRun 中止已发起 — 状态: ${data.status}`);
      qc.invalidateQueries({ queryKey: planRunKeys.detail(id) });
      qc.invalidateQueries({ queryKey: planRunKeys.timeline(id) });
      qc.invalidateQueries({ queryKey: planRunKeys.logsByRun(id) });
      qc.invalidateQueries({ queryKey: planRunKeys.devicesByRun(id) });
      invalidateSelectedJob();
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
      qc.invalidateQueries({ queryKey: planRunKeys.jobDetail(id, data.job_id) });
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
      qc.invalidateQueries({ queryKey: planRunKeys.jobDetail(id, data.job_id) });
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
      qc.invalidateQueries({ queryKey: planRunKeys.logsByRun(id) });
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

  // B3-3（契约 §5.1 规则 5）：全局心跳超时横幅由 Minimap `stuck_job_ids` 驱动，不依赖表格单页
  const stuckJobs = useMemo<PlanRunMinimapDevice[]>(() => {
    if (isTerminal || !minimapQ.data?.stuck_job_ids?.length) return [];
    const byJobId = new Map(minimapQ.data.devices.map((d) => [d.job_id, d]));
    return minimapQ.data.stuck_job_ids
      .map((jobId) => byJobId.get(jobId))
      .filter((d): d is PlanRunMinimapDevice => Boolean(d));
  }, [minimapQ.data, isTerminal]);

  // B3-3（契约 §5.3 规则 4）：选中详情加载成功后最多预取相邻 2 条单机详情（严禁预取时间线/异常/产物）
  const prefetchAdjacentJobs = useCallback(
    (neighborJobIds: number[]) => {
      if (!id) return;
      for (const neighborId of neighborJobIds.slice(0, 2)) {
        void qc.prefetchQuery({
          queryKey: planRunKeys.jobDetail(id, neighborId),
          queryFn: () => api.planRuns.getJobDetail(id, neighborId),
          staleTime: 10_000,
        });
      }
    },
    [id, qc],
  );

  const planName = useMemo(
    () => timelineQ.data?.plan_name ?? null,
    [timelineQ.data?.plan_name],
  );

  return {
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
  };
}
