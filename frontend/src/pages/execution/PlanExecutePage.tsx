import { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Skeleton } from '@/components/ui/skeleton';
import { StatusBadge } from '@/components/ui/status-badge';
import { PaginationBar } from '@/components/ui/pagination-bar';
import { useToast } from '@/hooks/useToast';
import { usePagination } from '@/hooks/usePagination';
import { api, ApiError, fetchAllDevices, fetchHostList, type HostActiveJob, type PlanRunPreview } from '@/utils/api';
import { deviceKeys, hostKeys, jobKeys, planKeys, planRunKeys } from '@/utils/api/queryKeys';
import { formatDurationSeconds } from '@/utils/format';
import { Smartphone, AlertCircle, ExternalLink, RefreshCw, Layers3, Trash2, ChevronLeft } from 'lucide-react';
import { PageContainer, PageHeader } from '@/components/layout';
import { STATUS_BG_COLORS } from '@/design-system/colors';
import { TEXT } from '@/design-system/tokens';
import { cn } from '@/lib/utils';
import { EmptyState } from '@/components/ui/empty-state';
import { ErrorState } from '@/components/ui/error-state';
import {
  compareNodeEntries,
  buildCapacityPlan,
  evaluateCapacityOverflow,
  evaluateDeviceReadiness,
  type ReadinessDevice,
} from '@/utils/planExecuteReadiness';
import { ExecuteCommandBar } from '@/components/execution/plan-execute/ExecuteCommandBar';
import { DeviceFilterBar } from '@/components/execution/plan-execute/DeviceFilterBar';
import { DeviceMatrix, applyMatrixSelection } from '@/components/execution/plan-execute/DeviceMatrix';
import { DispatchCockpit } from '@/components/execution/plan-execute/DispatchCockpit';
import { RecentPlanRunsInline } from '@/components/execution/plan-execute/RecentPlanRunsInline';
import { SelectedMinimap } from '@/components/execution/plan-execute/SelectedMinimap';
import { SelectionPresets } from '@/components/execution/plan-execute/SelectionPresets';
import { PlanStepList } from '@/components/execution/plan-execute/PlanStepList';
import {
  loadPlanExecuteDraft,
  removePlanExecuteDraft,
  savePlanExecuteDraft,
} from '@/components/execution/plan-execute/draft';
import {
  extractDispatchDeviceIds,
  findDuplicateMatch,
  pickRunsNeedingDeviceFetch,
  type DuplicateMatch,
} from '@/components/execution/plan-execute/planExecuteDuplicate';
import {
  buildDeviceSelectionCsv,
  downloadTextFile,
  formatSerialsClipboard,
} from '@/components/execution/plan-execute/planExecuteExport';
import {
  buildActiveFilterChips,
  clearActiveFilterChip,
  hasFilterQueryParams,
  parsePlanExecuteFilterParams,
  parseViewParam,
  writeFilterParamsToSearch,
  type ActiveFilterChipId,
} from '@/components/execution/plan-execute/planExecuteFilters';
import {
  shouldHandleEnterPrimary,
  shouldHandleSelectAllShortcut,
} from '@/components/execution/plan-execute/planExecuteKeyboard';
import {
  addPreset,
  applyPresetIntersection,
  deletePreset,
  loadPresets,
  type PlanExecutePreset,
} from '@/components/execution/plan-execute/planExecutePresets';
import { sortDevicesStable } from '@/components/execution/plan-execute/planExecuteSelection';
import { estimatePlanWallClock } from '@/components/execution/plan-execute/planExecuteWallClock';
import { isSchedulable } from '@/components/execution/plan-execute/tileStatus';
import {
  phaseIndex,
  type DeviceViewMode,
  type ExecutePhase,
  type PlanExecuteDraftV2,
} from '@/components/execution/plan-execute/types';

type DeviceSummary = ReadinessDevice;

function formatFailureThreshold(threshold: number | null | undefined): string {
  if (threshold == null) return '未设置（按默认 5% 生效）';
  return `${Math.round(threshold * 100)}%`;
}

export default function PlanExecutePage() {
  const navigate = useNavigate();
  const toast = useToast();
  const [searchParams, setSearchParams] = useSearchParams();
  // 草稿（sessionStorage）只在挂载时读取一次；恢复消费在下方单入口 effect 完成
  const draftRef = useRef<PlanExecuteDraftV2 | null | undefined>(undefined);
  if (draftRef.current === undefined) draftRef.current = loadPlanExecuteDraft();
  const lastClickedIndexRef = useRef<number | null>(null);
  // 清除草稿后置位，阻止防抖中的写入把草稿写回
  const suppressDraftWriteRef = useRef(false);
  const draftConsumedRef = useRef(false);
  const pendingLocateIdRef = useRef<number | null>(null);
  const highlightClearTimerRef = useRef<number | null>(null);
  const workspaceRef = useRef<HTMLDivElement | null>(null);
  const primaryActionRef = useRef<() => void>(() => {});
  const selectAllFilteredRef = useRef<() => void>(() => {});
  const primaryDisabledRef = useRef(false);

  const urlPlanId = searchParams.get('plan') ? Number(searchParams.get('plan')) : null;
  const [selectedPlanId, setSelectedPlanId] = useState<number | null>(
    // 规则：?plan= 与草稿不一致时以 URL 为准；设备集仍由草稿恢复
    urlPlanId ?? draftRef.current?.planId ?? null,
  );
  const [selectedDeviceIds, setSelectedDeviceIds] = useState<Set<number>>(new Set());
  const [phase, setPhase] = useState<ExecutePhase>('plan');
  const [nodeSearch, setNodeSearch] = useState('');
  const [planSearch, setPlanSearch] = useState('');
  // 复跑预填（?devices=1,2,3，来自 PlanRun 详情「复跑」）：只消费一次
  const prefillDevicesRef = useRef<string | null>(searchParams.get('devices'));

  // 恢复优先级：URL 筛选参数 >（?devices= 时默认空筛选）> 草稿
  const hasUrlDevices = prefillDevicesRef.current != null;
  const hasUrlFilters = hasFilterQueryParams(searchParams);
  const urlFilterState = parsePlanExecuteFilterParams(searchParams);
  const [view, setView] = useState<DeviceViewMode>(() => (
    searchParams.has('view')
      ? parseViewParam(searchParams.get('view'))
      : (hasUrlDevices ? 'matrix' : draftRef.current?.view ?? 'matrix')
  ));
  const [readyOnly, setReadyOnly] = useState(() => (
    hasUrlFilters ? urlFilterState.readyOnly : (hasUrlDevices ? false : Boolean(draftRef.current?.readyOnly))
  ));
  const [deviceFilter, setDeviceFilter] = useState(() => (
    hasUrlFilters ? urlFilterState.q : (hasUrlDevices ? '' : draftRef.current?.deviceFilter ?? '')
  ));
  const [deviceVersionFilter, setDeviceVersionFilter] = useState(() => (
    hasUrlFilters ? urlFilterState.version : (hasUrlDevices ? 'all' : draftRef.current?.deviceVersionFilter ?? 'all')
  ));
  const [deviceHostFilter, setDeviceHostFilter] = useState(() => (
    hasUrlFilters ? urlFilterState.host : (hasUrlDevices ? 'all' : draftRef.current?.deviceHostFilter ?? 'all')
  ));
  const [deviceModelFilter, setDeviceModelFilter] = useState(() => (
    hasUrlFilters ? urlFilterState.model : (hasUrlDevices ? 'all' : draftRef.current?.deviceModelFilter ?? 'all')
  ));
  const [deviceTagFilter, setDeviceTagFilter] = useState<string[]>(() => (
    hasUrlFilters ? urlFilterState.tags : (hasUrlDevices ? [] : draftRef.current?.deviceTagFilter ?? [])
  ));
  const [highlightId, setHighlightId] = useState<number | null>(null);
  const [presets, setPresets] = useState<PlanExecutePreset[]>(() => loadPresets());

  const [preview, setPreview] = useState<PlanRunPreview | null>(null);
  const [showClearConfirm, setShowClearConfirm] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [previewing, setPreviewing] = useState(false);
  const [runNote, setRunNote] = useState('');
  const [retryingDispatch, setRetryingDispatch] = useState(false);
  const [dispatchFailure, setDispatchFailure] = useState<{
    planRunId: number;
    message: string;
    retryable: boolean;
  } | null>(null);

  const {
    page: devicePage,
    pageSize: devicePageSize,
    skip: deviceSkip,
    totalPages: deviceTotalPages,
    setTotal: setDeviceTotal,
    goToPage: goToDevicePage,
    nextPage: nextDevicePage,
    prevPage: prevDevicePage,
    changePageSize: changeDevicePageSize,
    canPreviousPage: canPrevDevicePage,
    canNextPage: canNextDevicePage,
  } = usePagination({ initialPageSize: 50 });

  const {
    data: plans,
    isLoading: plansLoading,
    isError: plansError,
    error: plansQueryError,
    refetch: refetchPlans,
  } = useQuery({
    queryKey: planKeys.list(500),
    queryFn: () => api.plans.list(0, 500),
  });

  const { data: hostsList } = useQuery({
    queryKey: hostKeys.list(),
    queryFn: () => fetchHostList(0, 200),
  });

  // B1b：一次拉取全平台占用（独立 /jobs 路由，避免 hosts/{id} N+1）
  const { data: activeJobsByDevice } = useQuery({
    queryKey: jobKeys.activeByDevice(),
    queryFn: () => api.jobs.activeByDevice(),
    refetchInterval: 20_000,
  });
  const occupancyByDeviceId = useMemo(() => {
    const map = new Map<number, HostActiveJob>();
    for (const job of activeJobsByDevice ?? []) {
      map.set(job.device_id, job);
    }
    return map;
  }, [activeJobsByDevice]);

  const {
    data: devicesResp,
    isLoading: devLoading,
    isError: devicesError,
    error: devicesQueryError,
    refetch: refetchDevices,
  } = useQuery({
    queryKey: deviceKeys.all(),
    queryFn: () => fetchAllDevices(),
    refetchInterval: 20_000,
  });

  const selectedPlan = plans?.find(p => p.id === selectedPlanId);
  const executableStepCount =
    selectedPlan?.steps?.filter((step) => step.enabled !== false).length ?? 0;

  const { data: scriptsList } = useQuery({
    queryKey: ['scripts', 'active'],
    queryFn: () => api.scripts.list(true),
    enabled: phase === 'plan' && selectedPlanId != null,
    staleTime: 60_000,
  });
  const scriptParamsByKey = useMemo(() => {
    const map = new Map<string, Record<string, unknown>>();
    for (const script of scriptsList ?? []) {
      map.set(`${script.name}@${script.version}`, script.default_params ?? {});
    }
    return map;
  }, [scriptsList]);

  const {
    data: recentPlanRuns = [],
    isLoading: recentPlanRunsLoading,
  } = useQuery({
    queryKey: [...planRunKeys.list(), { planId: selectedPlanId, limit: 10 }],
    queryFn: () => api.planRuns.list(0, 10, selectedPlanId!),
    enabled: selectedPlanId != null,
    staleTime: 30_000,
  });

  const selectedDeviceIdsKey = useMemo(
    () => Array.from(selectedDeviceIds).sort((a, b) => a - b).join(','),
    [selectedDeviceIds],
  );

  useEffect(() => {
    setPreview(null);
  }, [selectedPlanId, selectedDeviceIdsKey]);

  const { data: duplicateMatch = null } = useQuery({
    queryKey: [
      'plan-execute-duplicate',
      selectedPlanId,
      selectedDeviceIdsKey,
      recentPlanRuns.map((run) => run.id).join(','),
    ],
    queryFn: async (): Promise<DuplicateMatch | null> => {
      const nowMs = Date.now();
      const needIds = pickRunsNeedingDeviceFetch(recentPlanRuns, nowMs);
      const details = needIds.length > 0
        ? await Promise.all(needIds.map((id) => api.planRuns.get(id)))
        : [];
      const byId = new Map(details.map((detail) => [detail.id, detail]));
      const candidates = recentPlanRuns.map((run) => {
        const enriched = byId.get(run.id) ?? run;
        return { run: enriched, deviceIds: extractDispatchDeviceIds(enriched) };
      });
      return findDuplicateMatch(selectedDeviceIds, candidates, nowMs);
    },
    enabled: selectedPlanId != null && selectedDeviceIds.size > 0 && recentPlanRuns.length > 0,
    staleTime: 15_000,
  });

  const filteredPlans = useMemo(() => {
    const keyword = planSearch.trim().toLowerCase();
    if (!keyword) return plans ?? [];
    return (plans ?? []).filter(p => p.name.toLowerCase().includes(keyword));
  }, [plans, planSearch]);

  const allDevices = useMemo(() => devicesResp ?? [], [devicesResp]);

  const schedulableDeviceIds = useMemo(
    () => new Set(allDevices.filter(isSchedulable).map((d: DeviceSummary) => d.id)),
    [allDevices],
  );

  const selectedSchedulableDeviceIds = useMemo(
    () => Array.from(selectedDeviceIds).filter(id => schedulableDeviceIds.has(id)),
    [selectedDeviceIds, schedulableDeviceIds],
  );

  const selectedDevices = useMemo(
    () => allDevices.filter((device: DeviceSummary) => selectedDeviceIds.has(device.id)),
    [allDevices, selectedDeviceIds],
  );
  const hostMap = useMemo(() => new Map((hostsList ?? []).map(host => [String(host.id), host])), [hostsList]);

  const devicesInHostScope = useMemo(
    () => allDevices.filter((device: DeviceSummary) =>
      deviceHostFilter === 'all' || String(device.host_id ?? 'unassigned') === deviceHostFilter),
    [allDevices, deviceHostFilter],
  );
  const versionOptions = useMemo(
    () => Array.from(new Set(devicesInHostScope.map((device: DeviceSummary) => device.build_display_id).filter(Boolean) as string[])).sort(),
    [devicesInHostScope],
  );
  const modelOptions = useMemo(
    () => Array.from(new Set(devicesInHostScope.map((device: DeviceSummary) => device.model).filter(Boolean) as string[])).sort(),
    [devicesInHostScope],
  );
  // 标签选项与版本/型号一致，随节点范围收窄
  const tagOptions = useMemo(
    () => Array.from(new Set(devicesInHostScope.flatMap((device: DeviceSummary) => device.tags ?? []))).sort(),
    [devicesInHostScope],
  );
  const hostOptions = useMemo(() => Array.from(new Map(allDevices.map((device: DeviceSummary) => {
    const id = String(device.host_id ?? 'unassigned');
    const host = hostMap.get(id);
    return [id, host?.ip || host?.name || (id === 'unassigned' ? '未分配节点' : id)];
  })).entries()), [allDevices, hostMap]);
  const nodeSummaries = useMemo(() => hostOptions.map(([id, label]) => {
    const devices = allDevices.filter((device: DeviceSummary) => String(device.host_id ?? 'unassigned') === id);
    const selected = devices.filter((device: DeviceSummary) => selectedDeviceIds.has(device.id)).length;
    const available = devices.filter(isSchedulable).length;
    const host = hostMap.get(id);
    return {
      id,
      label,
      total: devices.length,
      selected,
      available,
      online: !host || host.status === 'ONLINE',
      busy: host?.capacity?.active_jobs ?? 0,
      healthStatus: host?.health?.status ?? null,
      healthReasons: host?.health?.reasons ?? [],
    };
  }).sort(compareNodeEntries), [allDevices, hostMap, hostOptions, selectedDeviceIds]);
  const visibleNodeSummaries = useMemo(() => {
    const keyword = nodeSearch.trim().toLowerCase();
    if (!keyword) return nodeSummaries;
    return nodeSummaries.filter(node => node.label.toLowerCase().includes(keyword) || node.id.toLowerCase().includes(keyword));
  }, [nodeSearch, nodeSummaries]);
  const readinessResult = useMemo(
    () => evaluateDeviceReadiness(selectedDevices, hostsList ?? []),
    [hostsList, selectedDevices],
  );
  const readinessByDeviceId = useMemo(
    () => new Map(readinessResult.rows.map(row => [row.device.id, row])),
    [readinessResult.rows],
  );
  // 所选节点当前活跃任务合计（heartbeat capacity，信息参考，不阻塞发起）
  const selectedHostActiveJobs = useMemo(() => {
    const hostIds = new Set(selectedDevices.map((device: DeviceSummary) => String(device.host_id ?? 'unassigned')));
    let total = 0;
    hostIds.forEach(id => { total += hostMap.get(id)?.capacity?.active_jobs ?? 0; });
    return total;
  }, [hostMap, selectedDevices]);
  // 容量超限：本次选中数 > effective_slots（剩余可派发）；槽位缺失不告警
  const capacityOverflowWarnings = useMemo(
    () => evaluateCapacityOverflow(selectedDevices, hostsList ?? []),
    [hostsList, selectedDevices],
  );
  const capacityPlanRows = useMemo(
    () => buildCapacityPlan(selectedDevices, hostsList ?? []),
    [hostsList, selectedDevices],
  );
  const wallClockEstimate = useMemo(
    () => estimatePlanWallClock(recentPlanRuns),
    [recentPlanRuns],
  );

  const filterKeyword = deviceFilter.trim().toLowerCase();
  const baseFilteredDevices = allDevices.filter(d =>
    !filterKeyword
    || d.serial.toLowerCase().includes(filterKeyword)
    || (d.model ?? '').toLowerCase().includes(filterKeyword),
  ).filter((d: DeviceSummary) =>
    (deviceVersionFilter === 'all' || d.build_display_id === deviceVersionFilter)
    && (deviceHostFilter === 'all' || String(d.host_id ?? 'unassigned') === deviceHostFilter)
    && (deviceModelFilter === 'all' || d.model === deviceModelFilter)
    && (deviceTagFilter.length === 0 || (d.tags ?? []).some(tag => deviceTagFilter.includes(tag))),
  );
  const poolReadinessByDeviceId = useMemo(
    () => new Map(
      evaluateDeviceReadiness(baseFilteredDevices, hostsList ?? []).rows.map(row => [row.device.id, row]),
    ),
    [baseFilteredDevices, hostsList],
  );
  const filteredDevices = useMemo(() => {
    const list = readyOnly
      ? baseFilteredDevices.filter((d) => isSchedulable(d) && Boolean(poolReadinessByDeviceId.get(d.id)?.ready))
      : baseFilteredDevices;
    return sortDevicesStable(list, hostMap);
  }, [baseFilteredDevices, readyOnly, poolReadinessByDeviceId, hostMap]);
  const filteredAvailableIds = filteredDevices.filter(isSchedulable).map((device: DeviceSummary) => device.id);
  const readyFilteredIds = filteredDevices
    .filter((d) => isSchedulable(d) && Boolean(poolReadinessByDeviceId.get(d.id)?.ready))
    .map((d) => d.id);
  const allFilteredSelected = filteredAvailableIds.length > 0 && filteredAvailableIds.every(id => selectedDeviceIds.has(id));
  const versionChipOptions = versionOptions.slice(0, 6);
  const modelChipOptions = modelOptions.slice(0, 6);
  const selectedVersionCount = new Set(
    selectedDevices.map((device) => device.build_display_id).filter(Boolean),
  ).size;
  const versionConsistent = selectedDevices.length === 0 || selectedVersionCount <= 1;
  const pagedDevices = useMemo(
    () => filteredDevices.slice(deviceSkip, deviceSkip + devicePageSize),
    [filteredDevices, deviceSkip, devicePageSize],
  );
  // 预检前置可见：当前页设备在勾选前即计算 readiness，阻塞原因直接内联展示
  const pageReadinessByDeviceId = useMemo(
    () => new Map(
      evaluateDeviceReadiness(pagedDevices, hostsList ?? []).rows.map(row => [row.device.id, row]),
    ),
    [pagedDevices, hostsList],
  );

  useEffect(() => {
    setDeviceTotal(filteredDevices.length);
  }, [filteredDevices.length, setDeviceTotal]);

  useEffect(() => {
    goToDevicePage(1);
  }, [deviceFilter, deviceVersionFilter, deviceHostFilter, deviceModelFilter, deviceTagFilter, readyOnly, goToDevicePage]);

  useEffect(() => { lastClickedIndexRef.current = null; }, [deviceFilter, deviceVersionFilter, deviceHostFilter, deviceModelFilter, deviceTagFilter, readyOnly]);

  useEffect(() => {
    setSelectedDeviceIds(prev => {
      const removedIds = Array.from(prev).filter(id => !schedulableDeviceIds.has(id));
      if (removedIds.length === 0) return prev;
      const next = new Set(Array.from(prev).filter(id => schedulableDeviceIds.has(id)));
      const serials = removedIds.map(id => allDevices.find(d => d.id === id)?.serial ?? `#${id}`);
      const shown = serials.slice(0, 5).join('、');
      toast.info(
        `${removedIds.length} 台样机状态已变化，已从本次执行中移除：${shown}${serials.length > 5 ? ' 等' : ''}`,
      );
      return next;
    });
  }, [schedulableDeviceIds, allDevices, toast]);

  // 选择恢复单入口（设备与 Plan 首次加载完成后执行一次）：
  // 1. URL 含 devices（复跑）→ 走预填，忽略草稿，与草稿恢复互斥
  // 2. URL 仅含 plan → URL Plan 为准（state 初始化已处理），设备集仍从草稿恢复
  // 3. 无 URL 参数 → 整体读草稿
  useEffect(() => {
    if (devLoading || plansLoading) return;

    const restoreIds = (wantedIds: number[], lostLabel: (count: number, shown: string, more: boolean) => string) => {
      const byId = new Map(allDevices.map((device: DeviceSummary) => [device.id, device]));
      const restored: number[] = [];
      const lost: string[] = [];
      for (const id of wantedIds) {
        const device = byId.get(id);
        if (device && isSchedulable(device)) restored.push(id);
        else lost.push(device?.serial ?? `#${id}`);
      }
      if (lost.length > 0) {
        const shown = lost.slice(0, 5).join('、');
        toast.info(lostLabel(lost.length, shown, lost.length > 5));
      }
      return restored;
    };

    // 复跑预填（?devices=1,2,3）：URL 优先
    const raw = prefillDevicesRef.current;
    if (raw != null) {
      prefillDevicesRef.current = null;
      draftConsumedRef.current = true;
      const wantedIds = [...new Set(
        raw.split(',').map(Number).filter(n => Number.isInteger(n) && n > 0),
      )];
      if (wantedIds.length === 0) return;
      const restored = restoreIds(
        wantedIds,
        (count, shown, more) => `上次执行的样机中 ${count} 台本次不可用：${shown}${more ? ' 等' : ''}`,
      );
      if (restored.length === 0) return;
      setSelectedDeviceIds(new Set(restored));
      if (selectedPlan && executableStepCount > 0) setPhase('select');
      return;
    }

    // 草稿恢复
    if (draftConsumedRef.current) return;
    draftConsumedRef.current = true;
    const draft = draftRef.current ?? null;
    if (!draft) return;
    const restored = restoreIds(
      draft.deviceIds,
      (count, shown, more) => `草稿中 ${count} 台样机当前不可用，已移除：${shown}${more ? ' 等' : ''}`,
    );
    if (draft.view && !searchParams.has('view')) setView(draft.view);
    if (!hasFilterQueryParams(searchParams) && draft.readyOnly) setReadyOnly(Boolean(draft.readyOnly));
    if (restored.length > 0) {
      setSelectedDeviceIds(new Set(restored));
      if (selectedPlan && executableStepCount > 0 && draft.phase !== 'plan') setPhase(draft.phase);
    } else if (draft.phase === 'select' && selectedPlan && executableStepCount > 0) {
      setPhase('select');
    }
  }, [devLoading, plansLoading, allDevices, selectedPlan, executableStepCount, toast]);

  // 草稿写入（防抖 300ms）；清除后置位 suppress，防止写回
  useEffect(() => {
    if (suppressDraftWriteRef.current) return;
    const timer = setTimeout(() => {
      if (suppressDraftWriteRef.current) return;
      const draft: PlanExecuteDraftV2 = {
        planId: selectedPlanId,
        deviceIds: Array.from(selectedDeviceIds),
        phase,
        view,
        deviceFilter,
        deviceVersionFilter,
        deviceHostFilter,
        deviceModelFilter,
        deviceTagFilter,
        readyOnly,
      };
      savePlanExecuteDraft(draft);
    }, 300);
    return () => clearTimeout(timer);
  }, [selectedPlanId, selectedDeviceIds, phase, view, deviceFilter, deviceVersionFilter, deviceHostFilter, deviceModelFilter, deviceTagFilter, readyOnly]);

  // 筛选 / 视图写入 URL（replace，保留 plan/devices）；与草稿并行
  useEffect(() => {
    setSearchParams((prev) => {
      const next = writeFilterParamsToSearch(prev, {
        q: deviceFilter,
        version: deviceVersionFilter,
        model: deviceModelFilter,
        host: deviceHostFilter,
        tags: deviceTagFilter,
        readyOnly,
        view,
      });
      return next.toString() === prev.toString() ? prev : next;
    }, { replace: true });
  }, [deviceFilter, deviceVersionFilter, deviceHostFilter, deviceModelFilter, deviceTagFilter, readyOnly, view, setSearchParams]);

  const clearDraft = () => {
    suppressDraftWriteRef.current = true;
    removePlanExecuteDraft();
  };

  // 步骤 ≥2 时若已无选中样机，退回样机选择
  useEffect(() => {
    if (phase === 'dispatch' && selectedDevices.length === 0) {
      setPhase('select');
      toast.info('已无选中样机，已返回样机选择');
    }
  }, [phase, selectedDevices.length, toast]);

  const toggleDevice = (device: DeviceSummary) => {
    if (!isSchedulable(device)) return;
    setSelectedDeviceIds(prev => {
      const next = new Set(prev);
      if (next.has(device.id)) next.delete(device.id);
      else next.add(device.id);
      return next;
    });
  };

  const toggleAll = () => {
    const available = filteredDevices.filter(isSchedulable).map(d => d.id);
    const allSelected = available.length > 0 && available.every(id => selectedDeviceIds.has(id));
    if (allSelected) {
      setSelectedDeviceIds(prev => {
        const next = new Set(prev);
        available.forEach(id => next.delete(id));
        return next;
      });
    } else {
      setSelectedDeviceIds(prev => {
        const next = new Set(prev);
        available.forEach(id => next.add(id));
        return next;
      });
    }
  };
  // 移除样机（Minimap 方块 / 版本确认分组 / 移除阻塞）统一走撤销通道
  const removeDeviceIds = (ids: number[]) => {
    if (ids.length === 0) return;
    setSelectedDeviceIds(prev => {
      const next = new Set(prev);
      ids.forEach(id => next.delete(id));
      return next;
    });
    toast.action(`已移除 ${ids.length} 台样机`, {
      label: '撤销',
      onClick: () => setSelectedDeviceIds(prev => new Set([...prev, ...ids])),
    });
  };

  const handlePreview = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedPlanId) { toast.error('请选择 Plan'); return; }
    if (!selectedPlan || executableStepCount === 0) {
      toast.error('Plan 至少需要一个已启用步骤才能执行');
      return;
    }
    if (selectedSchedulableDeviceIds.length === 0) { toast.error('请至少选择一台设备'); return; }
    if (!readinessResult.passed) { toast.error('测试准备检查未通过'); return; }

    setPreviewing(true);
    try {
      const frozenDeviceIds = [...selectedSchedulableDeviceIds];
      const p = await api.plans.previewRun(selectedPlanId, {
        device_ids: frozenDeviceIds,
      });

      if (p.total_steps === 0) {
        toast.error('Plan 没有可执行步骤，无法发起');
        return;
      }
      if (p.device_ids?.length) {
        const expected = [...frozenDeviceIds].sort((a, b) => a - b);
        const actual = [...p.device_ids].sort((a, b) => a - b);
        if (expected.length !== actual.length || expected.some((id, index) => id !== actual[index])) {
          toast.error('预览返回的样机集合已发生变化，请重新检查并预览');
          return;
        }
      }
      setPreview({
        ...p,
        device_ids: frozenDeviceIds,
      });
      setDispatchFailure(null);
      toast.info('预览已生成，请核对驾驶舱后再次确认发起');
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : '预览失败');
    } finally {
      setPreviewing(false);
    }
  };

  const handleConfirm = async () => {
    if (!selectedPlanId || !preview || preview.total_steps === 0) return;
    setSubmitting(true);
    try {
      const trimmedNote = runNote.trim();
      const run = await api.plans.run(selectedPlanId, {
        device_ids: [...preview.device_ids],
        ...(trimmedNote ? { note: trimmedNote } : {}),
      });
      toast.success('Plan 已发起执行');
      clearDraft();
      navigate(`/execution/plan-runs/${run.id}`);
    } catch (err: unknown) {
      const apiError = err instanceof ApiError ? err : null;
      if (apiError?.status === 503 && apiError.planRunId != null) {
        setDispatchFailure({
          planRunId: apiError.planRunId,
          message: apiError.message,
          retryable: apiError.retryable !== false,
        });
        toast.error(apiError.message || '派发队列不可用');
      } else {
        toast.error(err instanceof Error ? err.message : '发起失败');
      }
    } finally {
      setSubmitting(false);
    }
  };

  const handleRetryDispatch = async () => {
    if (!dispatchFailure) return;
    setRetryingDispatch(true);
    try {
      await api.planRuns.retryDispatch(dispatchFailure.planRunId);
      toast.success('已重新入队派发门禁');
      navigate(`/execution/plan-runs/${dispatchFailure.planRunId}`);
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : '重试派发失败');
    } finally {
      setRetryingDispatch(false);
    }
  };

  const handlePhaseChange = (target: ExecutePhase) => {
    const targetIdx = phaseIndex(target);
    const currentIdx = phaseIndex(phase);
    if (targetIdx <= currentIdx) { setPhase(target); return; }
    if (!selectedPlanId || executableStepCount === 0) {
      toast.info('请先选择包含可执行步骤的测试计划');
      setPhase('plan');
      return;
    }
    if (target === 'dispatch' && selectedSchedulableDeviceIds.length === 0) {
      toast.info('请先选择至少一台可执行样机');
      setPhase('select');
      return;
    }
    setPhase(target);
  };

  const handleMatrixToggle = (device: DeviceSummary, event: { shiftKey: boolean }) => {
    setSelectedDeviceIds((prev) =>
      applyMatrixSelection(filteredDevices, prev, device, event, lastClickedIndexRef.current),
    );
  };

  const selectAllReady = () => {
    setSelectedDeviceIds((prev) => {
      const next = new Set(prev);
      readyFilteredIds.forEach((id) => next.add(id));
      return next;
    });
  };

  const selectAllFiltered = () => {
    setSelectedDeviceIds((prev) => {
      const next = new Set(prev);
      filteredAvailableIds.forEach((id) => next.add(id));
      return next;
    });
  };

  const handleSavePreset = (name: string) => {
    try {
      const created = addPreset(name, Array.from(selectedDeviceIds));
      setPresets(loadPresets());
      toast.success(`已保存方案「${created.name}」（${created.deviceIds.length} 台）`);
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : '保存方案失败');
    }
  };

  const handleApplyPreset = (preset: PlanExecutePreset) => {
    const { appliedIds, missingCount } = applyPresetIntersection(preset.deviceIds, schedulableDeviceIds);
    if (appliedIds.length === 0) {
      toast.info(`方案「${preset.name}」中的样机当前均不可调度`);
      return;
    }
    setSelectedDeviceIds(new Set(appliedIds));
    if (missingCount > 0) {
      toast.info(`已应用「${preset.name}」：${appliedIds.length} 台可用，${missingCount} 台已失效并跳过`);
    } else {
      toast.success(`已应用方案「${preset.name}」（${appliedIds.length} 台）`);
    }
  };

  const handleDeletePreset = (presetId: string) => {
    setPresets(deletePreset(presetId));
    toast.info('已删除选机方案');
  };

  const hostFilterLabel = useMemo(() => {
    if (deviceHostFilter === 'all') return undefined;
    const node = nodeSummaries.find((n) => n.id === deviceHostFilter);
    return node?.label ?? deviceHostFilter;
  }, [deviceHostFilter, nodeSummaries]);

  const activeFilterChips = useMemo(
    () => buildActiveFilterChips(
      {
        q: deviceFilter,
        version: deviceVersionFilter,
        model: deviceModelFilter,
        host: deviceHostFilter,
        tags: deviceTagFilter,
        readyOnly,
      },
      { hostLabel: hostFilterLabel },
    ),
    [deviceFilter, deviceVersionFilter, deviceModelFilter, deviceHostFilter, deviceTagFilter, readyOnly, hostFilterLabel],
  );

  const applyFilterChipClear = (chipId: ActiveFilterChipId) => {
    const next = clearActiveFilterChip(
      {
        q: deviceFilter,
        version: deviceVersionFilter,
        model: deviceModelFilter,
        host: deviceHostFilter,
        tags: deviceTagFilter,
        readyOnly,
        view,
      },
      chipId,
    );
    setDeviceFilter(next.q);
    setDeviceVersionFilter(next.version);
    setDeviceModelFilter(next.model);
    setDeviceHostFilter(next.host);
    setDeviceTagFilter(next.tags);
    setReadyOnly(next.readyOnly);
  };

  const flashHighlight = (deviceId: number) => {
    setHighlightId(deviceId);
    if (highlightClearTimerRef.current != null) window.clearTimeout(highlightClearTimerRef.current);
    highlightClearTimerRef.current = window.setTimeout(() => setHighlightId(null), 1600);
  };

  const locateInCurrentPool = (deviceId: number) => {
    flashHighlight(deviceId);
    if (view === 'table') {
      const idx = filteredDevices.findIndex((d) => d.id === deviceId);
      if (idx >= 0) {
        goToDevicePage(Math.floor(idx / devicePageSize) + 1);
      }
      window.requestAnimationFrame(() => {
        document
          .querySelector(`[data-device-row-id="${deviceId}"]`)
          ?.scrollIntoView?.({ behavior: 'smooth', block: 'center' });
      });
    }
  };

  const locateSelectedDevice = (deviceId: number) => {
    if (filteredDevices.some((d) => d.id === deviceId)) {
      pendingLocateIdRef.current = null;
      locateInCurrentPool(deviceId);
      return;
    }
    pendingLocateIdRef.current = deviceId;
    setDeviceHostFilter('all');
    setReadyOnly(false);
    setDeviceVersionFilter('all');
    setDeviceModelFilter('all');
    setDeviceTagFilter([]);
    setDeviceFilter('');
    toast.info('已清除筛选以定位该样机');
  };

  useEffect(() => {
    const pendingId = pendingLocateIdRef.current;
    if (pendingId == null) return;
    if (!filteredDevices.some((d) => d.id === pendingId)) return;
    pendingLocateIdRef.current = null;
    flashHighlight(pendingId);
    if (view === 'table') {
      const idx = filteredDevices.findIndex((d) => d.id === pendingId);
      if (idx >= 0) goToDevicePage(Math.floor(idx / devicePageSize) + 1);
      window.requestAnimationFrame(() => {
        const row = document.querySelector(`[data-device-row-id="${pendingId}"]`);
        if (row && typeof row.scrollIntoView === 'function') {
          row.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }
      });
    }
  }, [filteredDevices, view, devicePageSize, goToDevicePage]);

  const handleCopySerials = async () => {
    if (selectedDevices.length === 0) return;
    const text = formatSerialsClipboard(selectedDevices);
    try {
      await navigator.clipboard.writeText(text);
      toast.success(`已复制 ${selectedDevices.length} 个 serial`);
    } catch {
      toast.error('复制到剪贴板失败');
    }
  };

  const handleDownloadCsv = () => {
    if (selectedDevices.length === 0) return;
    const csv = buildDeviceSelectionCsv(selectedDevices, hostMap);
    downloadTextFile(`plan-execute-devices-${selectedDevices.length}.csv`, csv);
    toast.success(`已下载 ${selectedDevices.length} 台清单`);
  };

  const allNodesTotal = allDevices.length;
  const allNodesAvailable = schedulableDeviceIds.size;
  const allNodesSelected = selectedDeviceIds.size;

  const runPrimaryAction = () => {
    if (primaryDisabledRef.current) return;
    if (phase === 'plan') handlePhaseChange('select');
    else if (phase === 'select') handlePhaseChange('dispatch');
    else if (preview) void handleConfirm();
    else void handlePreview({ preventDefault() {} } as React.FormEvent);
  };

  const primaryDisabled =
    phase === 'plan'
      ? (!selectedPlanId || executableStepCount === 0)
      : phase === 'select'
        ? selectedSchedulableDeviceIds.length === 0
        : (previewing || submitting || !selectedPlanId || executableStepCount === 0 || selectedSchedulableDeviceIds.length === 0 || !readinessResult.passed);

  primaryActionRef.current = runPrimaryAction;
  selectAllFilteredRef.current = selectAllFiltered;
  primaryDisabledRef.current = primaryDisabled;

  // P5：Enter = 主 CTA；Ctrl/⌘+A = 全选当前筛选结果（限选机舞台）
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const hasOpenDialog = showClearConfirm
        || Boolean(document.querySelector('[role="dialog"][data-state="open"]'));

      if (shouldHandleSelectAllShortcut(event, {
        phase,
        workspace: workspaceRef.current,
      })) {
        event.preventDefault();
        selectAllFilteredRef.current();
        return;
      }

      if (shouldHandleEnterPrimary(event, { hasOpenDialog })) {
        event.preventDefault();
        primaryActionRef.current();
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [phase, showClearConfirm]);

  return (
    <PageContainer width="wide">
      <PageHeader title="执行 Plan" subtitle="选择已保存的 Plan 和目标样机，创建 PlanRun" />
      <ExecuteCommandBar
        phase={phase}
        onPhaseChange={handlePhaseChange}
        summary={{
          planName: selectedPlan?.name,
          selectedCount: selectedDevices.length,
          hostCount: readinessResult.byHost.length,
          versionCount: selectedVersionCount,
          versionConsistent,
          readyCount: readinessResult.readyCount,
          blockedCount: readinessResult.blockedCount,
          showDeviceMeta: phase !== 'plan',
        }}
        primaryLabel={
          phase === 'plan'
            ? '进入选机'
            : phase === 'select'
              ? '进入发起确认'
              : submitting
                ? '发起中...'
                : previewing
                  ? '预览中...'
                  : preview
                    ? '确认发起'
                    : '生成执行预览'
        }
        primaryLoading={phase === 'dispatch' && (previewing || submitting)}
        primaryDisabled={primaryDisabled}
        onPrimary={runPrimaryAction}
        secondary={
          phase === 'plan' ? (
            <Button
              type="button"
              variant="outline"
              onClick={() => {
                clearDraft();
                navigate('/orchestration/plans');
              }}
            >
              取消
            </Button>
          ) : (
            <Button
              type="button"
              variant="outline"
              onClick={() => handlePhaseChange(phase === 'dispatch' ? 'select' : 'plan')}
            >
              <ChevronLeft className="mr-1.5 h-4 w-4" />上一步
            </Button>
          )
        }
      />

      <Dialog open={showClearConfirm} onOpenChange={setShowClearConfirm}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>清空已选样机</DialogTitle>
            <DialogDescription>将移除本次已选的 {selectedDevices.length} 台样机，此操作不可撤销。</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setShowClearConfirm(false)}>取消</Button>
            <Button
              variant="destructive"
              onClick={() => {
                setSelectedDeviceIds(new Set());
                setShowClearConfirm(false);
              }}
            >
              确认清空
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {dispatchFailure && (
        <ErrorState
          title={`PlanRun #${dispatchFailure.planRunId} 派发失败`}
          description={dispatchFailure.message}
          action={
            <div className="flex justify-center gap-2">
              <Button
                type="button"
                variant="outline"
                onClick={() => navigate(`/execution/plan-runs/${dispatchFailure.planRunId}`)}
              >
                <ExternalLink className="mr-2 h-4 w-4" /> 查看详情
              </Button>
              {dispatchFailure.retryable && (
                <Button
                  type="button"
                  onClick={() => void handleRetryDispatch()}
                  disabled={retryingDispatch}
                >
                  <RefreshCw className={cn('mr-2 h-4 w-4', retryingDispatch && 'animate-spin')} />
                  {retryingDispatch ? '重试中…' : '重试派发'}
                </Button>
              )}
            </div>
          }
        />
      )}

      <form onSubmit={handlePreview} className="space-y-4">
        {phase === 'plan' && (
          <Card>
            <CardHeader><CardTitle className="text-base">Plan 配置</CardTitle></CardHeader>
            <CardContent>
              {plansLoading ? <Skeleton className="h-10 w-full" /> : plansError ? (
                <ErrorState
                  title="加载 Plan 失败"
                  description={(plansQueryError as Error)?.message || '请检查网络连接或稍后重试'}
                  onRetry={() => void refetchPlans()}
                />
              ) : (
                <>
                  <Input
                    className="mb-2"
                    value={planSearch}
                    onChange={event => setPlanSearch(event.target.value)}
                    placeholder="搜索 Plan 名称"
                  />
                  <Select
                    value={selectedPlanId != null ? String(selectedPlanId) : ''}
                    onValueChange={(v) => {
                      setSelectedPlanId(v ? Number(v) : null);
                      setPreview(null);
                      setDispatchFailure(null);
                    }}
                  >
                    <SelectTrigger className="w-full">
                      <SelectValue placeholder="— 请选择 Plan —" />
                    </SelectTrigger>
                    <SelectContent>
                      {filteredPlans.map(p => (
                        <SelectItem key={p.id} value={String(p.id)}>
                          {p.name}{p.steps?.length ? ` (${p.steps.length} 步骤)` : ''}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </>
              )}

              {selectedPlan?.description && (
                <p className={cn('mt-2 text-sm', TEXT.subtitle)}>{selectedPlan.description}</p>
              )}

              {selectedPlan && (
                <div className="mt-4 grid gap-4 lg:grid-cols-[220px_1fr]">
                  <div className="grid grid-cols-2 gap-2 lg:grid-cols-1">
                    <div className="rounded-lg bg-muted/50 p-3">
                      <div className={cn('text-xs', TEXT.subtitle)}>失败阈值</div>
                      <div className="mt-1 font-semibold">{formatFailureThreshold(selectedPlan.failure_threshold)}</div>
                    </div>
                    <div className="rounded-lg bg-muted/50 p-3">
                      <div className={cn('text-xs', TEXT.subtitle)}>启用步骤</div>
                      <div className="mt-1 font-semibold">{executableStepCount} / {selectedPlan.steps?.length ?? 0}</div>
                    </div>
                  </div>
                  <div className="rounded-lg border">
                    <div className="border-b px-3 py-2 text-sm font-medium">执行步骤</div>
                    <PlanStepList
                      steps={selectedPlan.steps}
                      scriptParamsByKey={scriptParamsByKey}
                    />
                  </div>
                </div>
              )}
              {selectedPlan && (
                <div className={cn('mt-3 flex flex-wrap gap-x-5 gap-y-1 text-xs', TEXT.subtitle)}>
                  <span>更新时间：{selectedPlan.updated_at ? new Date(selectedPlan.updated_at).toLocaleString() : '暂无记录'}</span>
                  <span>巡检周期：{formatDurationSeconds(selectedPlan.patrol_interval_seconds, 'precise', '未设置')}</span>
                  <span>超时：{formatDurationSeconds(selectedPlan.timeout_seconds, 'precise', '未设置')}</span>
                </div>
              )}

              {selectedPlan && (
                <div className="mt-4 rounded-lg border p-3">
                  <RecentPlanRunsInline
                    runs={recentPlanRuns}
                    loading={recentPlanRunsLoading}
                    onOpenRun={(runId) => navigate(`/execution/plan-runs/${runId}`)}
                  />
                </div>
              )}

              {selectedPlan && executableStepCount === 0 && (
                <div className={`mt-2 flex items-center gap-2 text-sm ${STATUS_BG_COLORS.warning} px-3 py-2 rounded-lg`}>
                  <AlertCircle className="w-4 h-4" /> 此 Plan 没有已启用步骤，无法执行
                </div>
              )}
            </CardContent>
          </Card>
        )}

        {phase === 'select' && (
          <Card>
            <CardHeader>
              <CardTitle className="text-base">
                <div className="flex items-center justify-between">
                  <span>样机选择</span>
                </div>
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div ref={workspaceRef} data-plan-execute-workspace>
              {devLoading ? <Skeleton className="h-40 w-full" /> : devicesError ? (
                <ErrorState
                  title="加载设备失败"
                  description={(devicesQueryError as Error)?.message || '请检查网络连接或稍后重试'}
                  onRetry={() => void refetchDevices()}
                />
              ) : allDevices.length === 0 ? (
                <EmptyState
                  title="暂无设备"
                  description="请先添加测试设备"
                  icon={<Smartphone className="w-12 h-12" />}
                />
              ) : (
                <div className="grid gap-4 lg:grid-cols-[260px_minmax(0,1fr)]">
                  <aside className="rounded-lg border bg-muted/20 p-2">
                    <div className="px-2 py-2 text-sm font-medium">搜索并选择节点</div>
                    <div className={cn('px-2 pb-2 text-xs', TEXT.subtitle)}>
                      已选 {selectedSchedulableDeviceIds.length} / {schedulableDeviceIds.size} 台可用
                    </div>
                    <Input
                      className="mb-2"
                      value={nodeSearch}
                      onChange={event => setNodeSearch(event.target.value)}
                      placeholder="节点 IP / 名称"
                      autoFocus
                    />
                    <div className="space-y-1">
                      <button
                        type="button"
                        onClick={() => setDeviceHostFilter('all')}
                        className={cn(
                          'w-full rounded-lg border px-3 py-2 text-left transition-colors',
                          deviceHostFilter === 'all' ? 'border-primary bg-primary/10' : 'border-transparent hover:bg-accent',
                        )}
                      >
                        <div className="flex items-center justify-between gap-2">
                          <span className="flex items-center gap-1.5 truncate text-xs font-medium">
                            <Layers3 className="h-3.5 w-3.5 shrink-0" />
                            全部节点
                          </span>
                        </div>
                        <div className={cn('mt-1 flex justify-between text-xs', TEXT.subtitle)}>
                          <span>{allNodesTotal} 台 · {allNodesAvailable} 可用</span>
                          <span>{allNodesSelected} 已选</span>
                        </div>
                        <div className="mt-2 h-1 overflow-hidden rounded bg-muted">
                          <div
                            className="h-full bg-primary"
                            style={{ width: `${allNodesTotal ? allNodesSelected / allNodesTotal * 100 : 0}%` }}
                          />
                        </div>
                      </button>
                      {visibleNodeSummaries.map(node => {
                        const unschedulable = node.healthStatus === 'UNSCHEDULABLE';
                        const degraded = node.healthStatus === 'DEGRADED';
                        const dotCls = !node.online || unschedulable ? 'bg-destructive' : degraded ? 'bg-warning' : 'bg-success';
                        const dotTitle = node.healthReasons.length
                          ? `${node.healthStatus}：${node.healthReasons.join('、')}`
                          : node.online ? '在线' : '离线';
                        return (
                          <button
                            key={node.id}
                            type="button"
                            onClick={() => setDeviceHostFilter(node.id)}
                            className={cn(
                              'w-full rounded-lg border px-3 py-2 text-left transition-colors',
                              deviceHostFilter === node.id ? 'border-primary bg-primary/10' : 'border-transparent hover:bg-accent',
                            )}
                          >
                            <div className="flex items-center justify-between gap-2">
                              <span className="truncate font-mono text-xs">{node.label}</span>
                              <span title={dotTitle} className={cn('h-2 w-2 shrink-0 rounded-full', dotCls)} />
                            </div>
                            <div className={cn('mt-1 flex justify-between text-xs', TEXT.subtitle)}>
                              <span>
                                {node.total} 台 · {node.available} 可用
                                {node.busy > 0 ? ` · 忙 ${node.busy}` : ''}
                              </span>
                              <span>{node.selected} 已选</span>
                            </div>
                            <div className="mt-2 h-1 overflow-hidden rounded bg-muted">
                              <div
                                className="h-full bg-primary"
                                style={{ width: `${node.total ? node.selected / node.total * 100 : 0}%` }}
                              />
                            </div>
                          </button>
                        );
                      })}
                    </div>
                    {visibleNodeSummaries.length === 0 && (
                      <div className={cn('px-2 py-6 text-center text-xs', TEXT.subtitle)}>未找到匹配节点</div>
                    )}
                  </aside>
                  <section className="min-w-0">
                    <div className="mb-3">
                      <DeviceFilterBar
                        deviceFilter={deviceFilter}
                        onDeviceFilterChange={setDeviceFilter}
                        deviceVersionFilter={deviceVersionFilter}
                        onVersionChange={setDeviceVersionFilter}
                        deviceModelFilter={deviceModelFilter}
                        onModelChange={setDeviceModelFilter}
                        deviceTagFilter={deviceTagFilter}
                        onTagFilterChange={setDeviceTagFilter}
                        versionOptions={versionOptions}
                        modelOptions={modelOptions}
                        tagOptions={tagOptions}
                        versionChips={versionChipOptions}
                        modelChips={modelChipOptions}
                        readyOnly={readyOnly}
                        onReadyOnlyChange={setReadyOnly}
                        view={view}
                        onViewChange={setView}
                        allFilteredSelected={allFilteredSelected}
                        filteredAvailableCount={filteredAvailableIds.length}
                        onToggleAll={toggleAll}
                        readyFilteredCount={readyFilteredIds.length}
                        onSelectAllReady={selectAllReady}
                        activeFilterChips={activeFilterChips}
                        onClearFilterChip={applyFilterChipClear}
                      />
                    </div>
                    <div className="mb-3">
                      <SelectedMinimap
                        devices={selectedDevices}
                        readinessByDeviceId={readinessByDeviceId}
                        hostMap={hostMap}
                        highlightId={highlightId}
                        onLocate={locateSelectedDevice}
                        onRemove={(id) => removeDeviceIds([id])}
                        onCopySerials={handleCopySerials}
                        onDownloadCsv={handleDownloadCsv}
                      />
                    </div>
                    <div className="mb-3">
                      <SelectionPresets
                        presets={presets}
                        selectedCount={selectedDeviceIds.size}
                        onSave={handleSavePreset}
                        onApply={handleApplyPreset}
                        onDelete={handleDeletePreset}
                      />
                    </div>
                    {view === 'matrix' ? (
                      <div className="mb-3 overflow-hidden rounded-lg border">
                        <DeviceMatrix
                          devices={filteredDevices}
                          selectedIds={selectedDeviceIds}
                          hostMap={hostMap}
                          readinessByDeviceId={poolReadinessByDeviceId}
                          occupancyByDeviceId={occupancyByDeviceId}
                          highlightId={highlightId}
                          onToggle={handleMatrixToggle}
                          lastClickedIndexRef={lastClickedIndexRef}
                        />
                      </div>
                    ) : (
                      <>
                    <div className="overflow-x-auto rounded-lg border">
                      <table className="w-full min-w-[800px] text-sm">
                        <thead className="bg-muted/95 text-left text-xs">
                          <tr>
                            <th className="w-10 px-3 py-2" />
                            <th className="px-3 py-2">Serial</th>
                            <th className="px-3 py-2">节点</th>
                            <th className="px-3 py-2">型号</th>
                            <th className="px-3 py-2">版本</th>
                            <th className="px-3 py-2">状态</th>
                            <th className="px-3 py-2">预检 / 占用</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y">
                          {pagedDevices.map((device: DeviceSummary) => {
                            const disabled = !isSchedulable(device);
                            const row = readinessByDeviceId.get(device.id) ?? pageReadinessByDeviceId.get(device.id);
                            const occupancy = occupancyByDeviceId.get(device.id);
                            const hostId = String(device.host_id ?? 'unassigned');
                            const host = hostMap.get(hostId);
                            const hostLabel = host?.ip || host?.name || (hostId === 'unassigned' ? '未分配节点' : hostId);
                            return (
                              <tr
                                key={device.id}
                                data-device-row-id={device.id}
                                className={cn(
                                  disabled ? 'opacity-50' : 'cursor-pointer hover:bg-accent/50',
                                  highlightId === device.id && 'animate-pulse bg-primary/10 ring-1 ring-inset ring-primary',
                                )}
                                onClick={() => toggleDevice(device)}
                              >
                                <td className="px-3 py-2">
                                  <input
                                    aria-label={`选择 ${device.serial}`}
                                    type="checkbox"
                                    checked={selectedDeviceIds.has(device.id)}
                                    disabled={disabled}
                                    readOnly
                                  />
                                </td>
                                <td className="px-3 py-2 font-mono text-xs">{device.serial}</td>
                                <td className="px-3 py-2 font-mono text-xs">{hostLabel}</td>
                                <td className="px-3 py-2">{device.model || '—'}</td>
                                <td className="px-3 py-2">{device.build_display_id || '—'}</td>
                                <td className="px-3 py-2"><StatusBadge kind="device" status={device.status} size="sm" /></td>
                                <td className={cn('px-3 py-2 text-xs', row?.ready ? 'text-success' : row ? 'text-destructive' : TEXT.subtitle)}>
                                  {occupancy?.plan_run_id != null ? (
                                    <a
                                      href={`/execution/plan-runs/${occupancy.plan_run_id}`}
                                      onClick={event => {
                                        event.stopPropagation();
                                        event.preventDefault();
                                        navigate(`/execution/plan-runs/${occupancy.plan_run_id}`);
                                      }}
                                      className="text-primary underline-offset-2 hover:underline"
                                    >
                                      执行中 · PlanRun #{occupancy.plan_run_id}
                                    </a>
                                  ) : row?.ready ? '就绪' : row ? row.reasons.join('、') : '选择后检查'}
                                </td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    </div>
                    {filteredDevices.length > 0 && (
                      <div className="mt-3">
                        <PaginationBar
                          page={devicePage}
                          totalPages={deviceTotalPages}
                          total={filteredDevices.length}
                          pageSize={devicePageSize}
                          canPreviousPage={canPrevDevicePage}
                          canNextPage={canNextDevicePage}
                          onGoToPage={goToDevicePage}
                          onNextPage={nextDevicePage}
                          onPrevPage={prevDevicePage}
                          onChangePageSize={changeDevicePageSize}
                          pageSizeOptions={[20, 50, 100]}
                        />
                      </div>
                    )}
                      </>
                    )}
                  </section>
                </div>
              )}
              </div>
            </CardContent>
          </Card>
        )}

        {phase === 'dispatch' && (
          <DispatchCockpit
            planName={selectedPlan?.name || '未选择'}
            executableStepCount={executableStepCount}
            devices={selectedDevices}
            capacityRows={capacityPlanRows}
            readyCount={readinessResult.readyCount}
            blockedCount={readinessResult.blockedCount}
            warnings={readinessResult.warnings}
            selectedHostActiveJobs={selectedHostActiveJobs}
            patrolIntervalSeconds={selectedPlan?.patrol_interval_seconds}
            timeoutSeconds={selectedPlan?.timeout_seconds}
            failureThreshold={selectedPlan?.failure_threshold}
            note={runNote}
            preview={preview}
            wallClock={wallClockEstimate}
            recentRuns={recentPlanRuns}
            recentRunsLoading={recentPlanRunsLoading}
            duplicateMatch={duplicateMatch}
            onNoteChange={setRunNote}
            onEditPlan={() => navigate(`/orchestration/plans/${selectedPlanId}`)}
            onOpenRun={(runId) => navigate(`/execution/plan-runs/${runId}`)}
            onRemoveBlocked={() => removeDeviceIds(readinessResult.blockedDeviceIds)}
          />
        )}

        {phase !== 'plan' && (
          <div className="sticky bottom-3 z-20 flex flex-col gap-3 rounded-xl border bg-background/95 p-3 shadow-lg backdrop-blur sm:flex-row sm:items-center">
            <div className="flex-1 text-sm">
              <span className="font-medium">已选 {selectedDevices.length} 台</span>
              <span className="mx-2 text-muted-foreground">|</span>
              <span className="text-success">{readinessResult.readyCount} 台就绪</span>
              <span className="mx-2 text-muted-foreground">|</span>
              <span className={readinessResult.blockedCount ? 'text-destructive' : TEXT.subtitle}>
                {readinessResult.blockedCount} 台阻塞
              </span>
              {capacityOverflowWarnings.length > 0 && (
                <>
                  <span className="mx-2 text-muted-foreground">|</span>
                  <span className="text-warning" title={capacityOverflowWarnings.map(w => w.message).join('；')}>
                    {capacityOverflowWarnings.length} 个节点超选（心跳参考）
                  </span>
                </>
              )}
            </div>
            {readinessResult.blockedCount > 0 && (
              <Button type="button" variant="outline" onClick={() => removeDeviceIds(readinessResult.blockedDeviceIds)}>
                <Trash2 className="mr-1.5 h-4 w-4" />移除阻塞设备
              </Button>
            )}
            {selectedDevices.length > 0 && (
              <Button type="button" variant="ghost" onClick={() => setShowClearConfirm(true)}>清空选择</Button>
            )}
          </div>
        )}
      </form>
    </PageContainer>
  );
}
