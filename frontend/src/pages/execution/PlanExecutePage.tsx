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
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import { Skeleton } from '@/components/ui/skeleton';
import { StatusBadge } from '@/components/ui/status-badge';
import { PaginationBar } from '@/components/ui/pagination-bar';
import { useToast } from '@/hooks/useToast';
import { usePagination } from '@/hooks/usePagination';
import { api, ApiError, fetchAllDevices, fetchHostList, type HostActiveJob, type PlanRunPreview } from '@/utils/api';
import { deviceKeys, hostKeys, jobKeys, planKeys } from '@/utils/api/queryKeys';
import { formatDurationSeconds } from '@/utils/format';
import { Play, Smartphone, AlertCircle, Eye, ExternalLink, RefreshCw, Layers3, Trash2, ChevronLeft, ChevronRight, Check, ChevronDown, Loader2, X } from 'lucide-react';
import { PageContainer, PageHeader } from '@/components/layout';
import { STATUS_BG_COLORS } from '@/design-system/colors';
import { TEXT } from '@/design-system/tokens';
import { cn } from '@/lib/utils';
import { EmptyState } from '@/components/ui/empty-state';
import { ErrorState } from '@/components/ui/error-state';
import {
  compareNodeEntries,
  evaluateCapacityOverflow,
  evaluateDeviceReadiness,
  type ReadinessDevice,
} from '@/utils/planExecuteReadiness';
import { PlanExecuteWizardNav, WIZARD_STEPS } from '@/components/execution/PlanExecuteWizardNav';

type DeviceSummary = ReadinessDevice;

const isSchedulable = (device: DeviceSummary) =>
  typeof device.schedulable === 'boolean'
    ? device.schedulable
    : device.status === 'ONLINE';

function formatFailureThreshold(threshold: number | null | undefined): string {
  if (threshold == null) return '未设置（按默认 5% 生效）';
  return `${Math.round(threshold * 100)}%`;
}

const DRAFT_KEY = 'stp.planExecute.draft.v1';

interface PlanExecuteDraft {
  planId: number | null;
  deviceIds: number[];
  currentStep: number;
  deviceFilter: string;
  deviceVersionFilter: string;
  deviceHostFilter: string;
  deviceModelFilter: string;
  deviceTagFilter: string[];
}

function loadPlanExecuteDraft(): PlanExecuteDraft | null {
  try {
    const raw = sessionStorage.getItem(DRAFT_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<PlanExecuteDraft> | null;
    if (typeof parsed !== 'object' || parsed === null) return null;
    return {
      planId: typeof parsed.planId === 'number' ? parsed.planId : null,
      deviceIds: Array.isArray(parsed.deviceIds)
        ? parsed.deviceIds.filter((id): id is number => Number.isInteger(id) && id > 0)
        : [],
      currentStep: Number.isInteger(parsed.currentStep)
        ? Math.min(Math.max(Number(parsed.currentStep), 0), WIZARD_STEPS.length - 1)
        : 0,
      deviceFilter: typeof parsed.deviceFilter === 'string' ? parsed.deviceFilter : '',
      deviceVersionFilter: typeof parsed.deviceVersionFilter === 'string' ? parsed.deviceVersionFilter : 'all',
      deviceHostFilter: typeof parsed.deviceHostFilter === 'string' ? parsed.deviceHostFilter : 'all',
      deviceModelFilter: typeof parsed.deviceModelFilter === 'string' ? parsed.deviceModelFilter : 'all',
      deviceTagFilter: Array.isArray(parsed.deviceTagFilter)
        ? parsed.deviceTagFilter.filter((tag): tag is string => typeof tag === 'string')
        : [],
    };
  } catch {
    return null;
  }
}

function removePlanExecuteDraft() {
  try {
    sessionStorage.removeItem(DRAFT_KEY);
  } catch {
    /* ignore */
  }
}

function PreviewDialog({
  open,
  preview,
  submitting,
  failureThreshold,
  patrolIntervalSeconds,
  timeoutSeconds,
  note = '',
  groups,
  devices,
  onClose,
  onConfirm,
}: {
  open: boolean;
  preview: PlanRunPreview | null;
  submitting: boolean;
  failureThreshold: number | null | undefined;
  patrolIntervalSeconds: number | null | undefined;
  timeoutSeconds: number | null | undefined;
  note?: string;
  groups: Array<{ key: string; hostLabel: string; model: string; version: string; total: number; ready: number }>;
  devices: DeviceSummary[];
  onClose: () => void;
  onConfirm: () => void;
}) {
  const deviceJobCount = preview?.device_count ?? preview?.job_count ?? '—';
  return (
    <Dialog open={open && preview != null} onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>确认执行</DialogTitle>
          <DialogDescription>{preview?.plan_name}</DialogDescription>
        </DialogHeader>
        <div className="space-y-3 text-sm">
          <div className="flex justify-between">
            <span className="text-muted-foreground">设备数（= Job 数）</span>
            <span className="font-medium">{deviceJobCount}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-muted-foreground">总步骤数</span>
            <span className="font-medium">{preview?.total_steps ?? '—'}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-muted-foreground">失败阈值</span>
            <span className="font-medium">{formatFailureThreshold(failureThreshold)}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-muted-foreground">巡检周期</span>
            <span className="font-medium">{formatDurationSeconds(patrolIntervalSeconds, 'precise', '未设置')}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-muted-foreground">超时</span>
            <span className="font-medium">{formatDurationSeconds(timeoutSeconds, 'precise', '未设置')}</span>
          </div>
          {note.trim() ? (
            <div className="rounded-lg border px-3 py-2">
              <div className="text-muted-foreground">执行备注</div>
              <div className="mt-1 whitespace-pre-wrap break-words">{note.trim()}</div>
            </div>
          ) : null}
          <div className="rounded-lg border">
            <div className="border-b px-3 py-2 font-medium">节点 / 型号 / 版本分布</div>
            <div className="max-h-40 divide-y overflow-auto">
              {groups.map(group => (
                <div key={group.key} className="grid grid-cols-[1fr_1fr_1fr_auto] gap-2 px-3 py-2 text-xs">
                  <span>{group.hostLabel}</span>
                  <span>{group.model}</span>
                  <span className="truncate" title={group.version}>{group.version}</span>
                  <span>{group.total} 台</span>
                </div>
              ))}
            </div>
          </div>
          <details className="rounded-lg border">
            <summary className="cursor-pointer px-3 py-2 font-medium">查看设备 Serial（{devices.length}）</summary>
            <div className="max-h-32 overflow-auto border-t p-3 font-mono text-xs leading-6">
              {devices.map(device => <div key={device.id}>{device.serial}</div>)}
            </div>
          </details>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>取消</Button>
          <Button onClick={onConfirm} disabled={submitting}>
            <Play className="w-4 h-4 mr-1.5" />{submitting ? '发起中...' : '确认发起'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default function PlanExecutePage() {
  const navigate = useNavigate();
  const toast = useToast();
  const [searchParams] = useSearchParams();
  // 草稿（sessionStorage）只在挂载时读取一次；恢复消费在下方单入口 effect 完成
  const draftRef = useRef<PlanExecuteDraft | null | undefined>(undefined);
  if (draftRef.current === undefined) draftRef.current = loadPlanExecuteDraft();
  // 清除草稿后置位，阻止防抖中的写入把草稿写回
  const suppressDraftWriteRef = useRef(false);
  const draftConsumedRef = useRef(false);

  const urlPlanId = searchParams.get('plan') ? Number(searchParams.get('plan')) : null;
  const [selectedPlanId, setSelectedPlanId] = useState<number | null>(
    // 规则：?plan= 与草稿不一致时以 URL 为准；设备集仍由草稿恢复
    urlPlanId ?? draftRef.current?.planId ?? null,
  );
  const [selectedDeviceIds, setSelectedDeviceIds] = useState<Set<number>>(new Set());
  const [currentStep, setCurrentStep] = useState(0);
  const [nodeSearch, setNodeSearch] = useState('');
  const [planSearch, setPlanSearch] = useState('');
  // 复跑预填（?devices=1,2,3，来自 PlanRun 详情「复跑」）：只消费一次
  const prefillDevicesRef = useRef<string | null>(searchParams.get('devices'));

  // 严格整份忽略草稿：?devices= 复跑路径下筛选器不继承草稿（仅无 URL 设备参数时恢复）
  const hasUrlDevices = prefillDevicesRef.current != null;
  const [deviceFilter, setDeviceFilter] = useState(() => (hasUrlDevices ? '' : draftRef.current?.deviceFilter ?? ''));
  const [deviceVersionFilter, setDeviceVersionFilter] = useState(() => (hasUrlDevices ? 'all' : draftRef.current?.deviceVersionFilter ?? 'all'));
  const [deviceHostFilter, setDeviceHostFilter] = useState(() => (hasUrlDevices ? 'all' : draftRef.current?.deviceHostFilter ?? 'all'));
  const [deviceModelFilter, setDeviceModelFilter] = useState(() => (hasUrlDevices ? 'all' : draftRef.current?.deviceModelFilter ?? 'all'));
  const [deviceTagFilter, setDeviceTagFilter] = useState<string[]>(() => (hasUrlDevices ? [] : draftRef.current?.deviceTagFilter ?? []));

  const [preview, setPreview] = useState<PlanRunPreview | null>(null);
  const [showPreview, setShowPreview] = useState(false);
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
    enabled: currentStep === 0 && selectedPlanId != null,
    staleTime: 60_000,
  });
  const scriptParamsByKey = useMemo(() => {
    const map = new Map<string, Record<string, unknown>>();
    for (const script of scriptsList ?? []) {
      map.set(`${script.name}@${script.version}`, script.default_params ?? {});
    }
    return map;
  }, [scriptsList]);

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
  const selectedGroups = useMemo(() => {
    const groups = new Map<string, { key: string; hostLabel: string; model: string; version: string; total: number; ready: number; ids: number[] }>();
    for (const row of readinessResult.rows) {
      const hostId = String(row.device.host_id ?? 'unassigned');
      const host = hostMap.get(hostId);
      const hostLabel = host?.ip || host?.name || (hostId === 'unassigned' ? '未分配节点' : hostId);
      const model = row.device.model || '型号未知';
      const version = row.device.build_display_id || '版本未知';
      const key = `${hostId}\u0000${model}\u0000${version}`;
      const group = groups.get(key) ?? { key, hostLabel, model, version, total: 0, ready: 0, ids: [] };
      group.total += 1;
      group.ready += row.ready ? 1 : 0;
      group.ids.push(row.device.id);
      groups.set(key, group);
    }
    return Array.from(groups.values());
  }, [hostMap, readinessResult.rows]);

  const filterKeyword = deviceFilter.trim().toLowerCase();
  const filteredDevices = allDevices.filter(d =>
    !filterKeyword
    || d.serial.toLowerCase().includes(filterKeyword)
    || (d.model ?? '').toLowerCase().includes(filterKeyword),
  ).filter((d: DeviceSummary) =>
    (deviceVersionFilter === 'all' || d.build_display_id === deviceVersionFilter)
    && (deviceHostFilter === 'all' || String(d.host_id ?? 'unassigned') === deviceHostFilter)
    && (deviceModelFilter === 'all' || d.model === deviceModelFilter)
    && (deviceTagFilter.length === 0 || (d.tags ?? []).some(tag => deviceTagFilter.includes(tag))),
  );
  const filteredAvailableIds = filteredDevices.filter(isSchedulable).map((device: DeviceSummary) => device.id);
  const allFilteredSelected = filteredAvailableIds.length > 0 && filteredAvailableIds.every(id => selectedDeviceIds.has(id));
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
  }, [deviceFilter, deviceVersionFilter, deviceHostFilter, deviceModelFilter, deviceTagFilter, goToDevicePage]);

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
      if (selectedPlan && executableStepCount > 0) setCurrentStep(2);
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
    if (restored.length > 0) {
      setSelectedDeviceIds(new Set(restored));
      if (selectedPlan && executableStepCount > 0 && draft.currentStep > 0) setCurrentStep(draft.currentStep);
    } else if (draft.currentStep === 1 && selectedPlan && executableStepCount > 0) {
      setCurrentStep(1);
    }
  }, [devLoading, plansLoading, allDevices, selectedPlan, executableStepCount, toast]);

  // 草稿写入（防抖 300ms）；清除后置位 suppress，防止写回
  useEffect(() => {
    if (suppressDraftWriteRef.current) return;
    const timer = setTimeout(() => {
      if (suppressDraftWriteRef.current) return;
      const draft: PlanExecuteDraft = {
        planId: selectedPlanId,
        deviceIds: Array.from(selectedDeviceIds),
        currentStep,
        deviceFilter,
        deviceVersionFilter,
        deviceHostFilter,
        deviceModelFilter,
        deviceTagFilter,
      };
      try {
        sessionStorage.setItem(DRAFT_KEY, JSON.stringify(draft));
      } catch {
        /* 忽略配额/隐私模式异常 */
      }
    }, 300);
    return () => clearTimeout(timer);
  }, [selectedPlanId, selectedDeviceIds, currentStep, deviceFilter, deviceVersionFilter, deviceHostFilter, deviceModelFilter, deviceTagFilter]);

  const clearDraft = () => {
    suppressDraftWriteRef.current = true;
    removePlanExecuteDraft();
  };

  // 步骤 ≥2 时若已无选中样机，退回样机选择
  useEffect(() => {
    if (currentStep >= 2 && selectedDevices.length === 0) {
      setCurrentStep(1);
      toast.info('已无选中样机，已返回样机选择');
    }
  }, [currentStep, selectedDevices.length, toast]);

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
      setShowPreview(true);
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
      setShowPreview(false);
      navigate(`/execution/plan-runs/${run.id}`);
    } catch (err: unknown) {
      const apiError = err instanceof ApiError ? err : null;
      if (apiError?.status === 503 && apiError.planRunId != null) {
        setShowPreview(false);
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

  const handleStepChange = (target: number) => {
    if (target <= currentStep) { setCurrentStep(target); return; }
    if (!selectedPlanId || executableStepCount === 0) { toast.info('请先选择包含可执行步骤的测试计划'); setCurrentStep(0); return; }
    if (target >= 2 && selectedSchedulableDeviceIds.length === 0) { toast.info('请先选择至少一台可执行样机'); setCurrentStep(1); return; }
    setCurrentStep(target);
  };

  const allNodesTotal = allDevices.length;
  const allNodesAvailable = schedulableDeviceIds.size;
  const allNodesSelected = selectedDeviceIds.size;

  return (
    <PageContainer width="wide">
      <PreviewDialog
        open={showPreview}
        preview={preview}
        submitting={submitting}
        failureThreshold={selectedPlan?.failure_threshold}
        patrolIntervalSeconds={selectedPlan?.patrol_interval_seconds}
        timeoutSeconds={selectedPlan?.timeout_seconds}
        note={runNote}
        groups={selectedGroups}
        devices={selectedDevices}
        onClose={() => setShowPreview(false)}
        onConfirm={handleConfirm}
      />

      <PageHeader title="执行 Plan" subtitle="选择已保存的 Plan 和目标样机，创建 PlanRun" />
      <PlanExecuteWizardNav currentStep={currentStep} onStepChange={handleStepChange} />

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
        {currentStep === 0 && (
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
                    <div className="max-h-72 divide-y overflow-y-auto">
                      {selectedPlan.steps?.map((step, index) => {
                        const paramsKey = step.script_name && step.script_version
                          ? `${step.script_name}@${step.script_version}`
                          : '';
                        const defaultParams = paramsKey ? scriptParamsByKey.get(paramsKey) : undefined;
                        const paramsJson = defaultParams && Object.keys(defaultParams).length > 0
                          ? JSON.stringify(defaultParams, null, 2)
                          : null;
                        return (
                          <details
                            key={step.id ?? step.step_key}
                            className={cn(step.enabled === false && 'opacity-50')}
                          >
                            <summary className="grid cursor-pointer grid-cols-[32px_80px_1fr_auto] items-center gap-2 px-3 py-2 text-xs">
                              <span>{index + 1}</span>
                              <span>{step.stage}</span>
                              <span className="truncate">{step.script_name} · {step.script_version}</span>
                              <span>{step.enabled === false ? '停用' : '启用'}</span>
                            </summary>
                            <div className="border-t bg-muted/30 px-3 py-2">
                              <div className={cn('mb-1 text-[11px]', TEXT.subtitle)}>default_params（只读）</div>
                              {paramsJson ? (
                                <pre className="max-h-40 overflow-auto rounded border bg-background p-2 font-mono text-[11px] leading-5">
                                  {paramsJson}
                                </pre>
                              ) : (
                                <div className={cn('text-[11px]', TEXT.subtitle)}>
                                  {paramsKey ? '该脚本版本无 default_params 或尚未加载' : '步骤缺少脚本引用'}
                                </div>
                              )}
                            </div>
                          </details>
                        );
                      })}
                    </div>
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

              {selectedPlan && executableStepCount === 0 && (
                <div className={`mt-2 flex items-center gap-2 text-sm ${STATUS_BG_COLORS.warning} px-3 py-2 rounded-lg`}>
                  <AlertCircle className="w-4 h-4" /> 此 Plan 没有已启用步骤，无法执行
                </div>
              )}
            </CardContent>
          </Card>
        )}

        {currentStep === 1 && (
          <Card>
            <CardHeader>
              <CardTitle className="text-base">
                <div className="flex items-center justify-between">
                  <span>样机选择</span>
                </div>
              </CardTitle>
            </CardHeader>
            <CardContent>
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
                    <div className="mb-3 flex flex-wrap items-center gap-2">
                      <Input
                        className="min-w-48 flex-1"
                        type="text"
                        placeholder="搜索 Serial / 型号"
                        value={deviceFilter}
                        onChange={event => setDeviceFilter(event.target.value)}
                      />
                      <Select value={deviceVersionFilter} onValueChange={setDeviceVersionFilter}>
                        <SelectTrigger className="w-44"><SelectValue /></SelectTrigger>
                        <SelectContent>
                          <SelectItem value="all">全部版本</SelectItem>
                          {versionOptions.map(value => <SelectItem key={value} value={value}>{value}</SelectItem>)}
                        </SelectContent>
                      </Select>
                      <Select value={deviceModelFilter} onValueChange={setDeviceModelFilter}>
                        <SelectTrigger className="w-40"><SelectValue /></SelectTrigger>
                        <SelectContent>
                          <SelectItem value="all">全部型号</SelectItem>
                          {modelOptions.map(value => <SelectItem key={value} value={value}>{value}</SelectItem>)}
                        </SelectContent>
                      </Select>
                      <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                          <Button type="button" variant="outline" className="w-40 justify-between font-normal">
                            <span className="truncate">
                              {deviceTagFilter.length === 0
                                ? '全部标签'
                                : deviceTagFilter.length === 1
                                  ? deviceTagFilter[0]
                                  : `已选 ${deviceTagFilter.length} 个标签`}
                            </span>
                            <ChevronDown className="ml-2 h-4 w-4 shrink-0 opacity-50" />
                          </Button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="start" className="w-48">
                          <DropdownMenuItem onSelect={() => setDeviceTagFilter([])}>
                            <span className="mr-2 inline-flex w-4 justify-center">
                              {deviceTagFilter.length === 0 && <Check className="h-4 w-4" />}
                            </span>
                            全部标签
                          </DropdownMenuItem>
                          <DropdownMenuSeparator />
                          {tagOptions.map(tag => {
                            const active = deviceTagFilter.includes(tag);
                            return (
                              <DropdownMenuItem
                                key={tag}
                                onSelect={(event) => {
                                  event.preventDefault();
                                  setDeviceTagFilter(prev => (active ? prev.filter(t => t !== tag) : [...prev, tag]));
                                }}
                              >
                                <span className="mr-2 inline-flex w-4 justify-center">
                                  {active && <Check className="h-4 w-4" />}
                                </span>
                                <span className="truncate">{tag}</span>
                              </DropdownMenuItem>
                            );
                          })}
                          {tagOptions.length === 0 && (
                            <DropdownMenuItem disabled>暂无标签</DropdownMenuItem>
                          )}
                        </DropdownMenuContent>
                      </DropdownMenu>
                      <Button type="button" variant="outline" onClick={toggleAll}>
                        {allFilteredSelected
                          ? `取消选择筛选结果 (${filteredAvailableIds.length})`
                          : `全选筛选结果 (${filteredAvailableIds.length})`}
                      </Button>
                    </div>
                    <div className="mb-3 rounded-lg border p-3">
                      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                        <div>
                          <div className="text-sm font-medium">已选样机 Minimap</div>
                          <div className={cn('text-xs', TEXT.subtitle)}>
                            跨节点汇总本次已选的 {selectedDevices.length} 台样机 · 点击方块可从本次测试中移除
                          </div>
                        </div>
                        <div className="flex gap-3 text-xs">
                          <span><i className="mr-1 inline-block h-2.5 w-2.5 rounded-sm bg-success" />已选就绪</span>
                          <span className="inline-flex items-center gap-1">
                            <i
                              className="inline-block h-2.5 w-2.5 rounded-sm border border-destructive bg-destructive"
                              style={{
                                backgroundImage:
                                  'repeating-linear-gradient(45deg, transparent, transparent 1px, rgba(255,255,255,0.55) 1px, rgba(255,255,255,0.55) 2px)',
                              }}
                            />
                            已选阻塞（斜纹 / ✕）
                          </span>
                        </div>
                      </div>
                      {selectedDevices.length === 0 ? (
                        <div className={cn('flex min-h-20 items-center justify-center text-xs', TEXT.subtitle)}>尚未选择样机</div>
                      ) : (
                        <TooltipProvider delayDuration={200}>
                          <div className="grid gap-1" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(28px, 1fr))' }}>
                            {selectedDevices.map((device: DeviceSummary) => {
                              const row = readinessByDeviceId.get(device.id);
                              const blocked = Boolean(row && !row.ready);
                              const host = hostMap.get(String(device.host_id));
                              const hostLabel = host?.ip || host?.name || device.host_id || '节点未知';
                              return (
                                <Tooltip key={device.id}>
                                  <TooltipTrigger asChild>
                                    <button
                                      type="button"
                                      aria-label={`已选设备方块 ${device.id}${blocked ? ' 阻塞' : ''}`}
                                      onClick={() => removeDeviceIds([device.id])}
                                      className={cn(
                                        'relative flex aspect-square items-center justify-center overflow-hidden rounded-sm border transition-transform hover:scale-110 hover:ring-2 hover:ring-primary/40',
                                        blocked ? 'border-destructive bg-destructive' : 'border-success bg-success',
                                      )}
                                    >
                                      {blocked && (
                                        <>
                                          <span
                                            aria-hidden
                                            className="pointer-events-none absolute inset-0 opacity-70"
                                            style={{
                                              backgroundImage:
                                                'repeating-linear-gradient(45deg, transparent, transparent 2px, rgba(255,255,255,0.45) 2px, rgba(255,255,255,0.45) 4px)',
                                            }}
                                          />
                                          <X className="relative h-3 w-3 text-primary-foreground drop-shadow" strokeWidth={3} aria-hidden />
                                        </>
                                      )}
                                    </button>
                                  </TooltipTrigger>
                                  <TooltipContent side="top" className="max-w-xs space-y-0.5 bg-popover text-popover-foreground">
                                    <div className="font-mono text-xs font-medium">{device.serial}</div>
                                    <div className="text-[11px]">节点：{hostLabel}</div>
                                    <div className="text-[11px]">型号：{device.model || '—'}</div>
                                    <div className="text-[11px]">版本：{device.build_display_id || '—'}</div>
                                    {blocked && row?.reasons?.[0] ? (
                                      <div className="text-[11px] text-destructive">阻塞：{row.reasons[0]}</div>
                                    ) : null}
                                  </TooltipContent>
                                </Tooltip>
                              );
                            })}
                          </div>
                        </TooltipProvider>
                      )}
                    </div>
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
                                className={cn(disabled ? 'opacity-50' : 'cursor-pointer hover:bg-accent/50')}
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
                  </section>
                </div>
              )}
            </CardContent>
          </Card>
        )}

        {currentStep === 2 && (
          <Card>
            <CardHeader><CardTitle className="text-base">节点数量与版本一致性确认</CardTitle></CardHeader>
            <CardContent className="space-y-4">
              {selectedGroups.length === 0 ? (
                <EmptyState
                  title="尚未选择样机"
                  description="请返回“样机选择”，按节点选择测试样机"
                  icon={<Smartphone className="h-10 w-10" />}
                />
              ) : (
                <>
                  <div className="overflow-x-auto rounded-lg border">
                    <table className="w-full min-w-[720px] text-sm">
                      <thead className="bg-muted/70 text-left text-xs">
                        <tr>
                          <th className="px-3 py-2">节点 IP</th>
                          <th className="px-3 py-2">型号</th>
                          <th className="px-3 py-2">版本</th>
                          <th className="px-3 py-2">选择数量</th>
                          <th className="px-3 py-2">就绪</th>
                          <th className="w-10 px-2 py-2" />
                        </tr>
                      </thead>
                      <tbody className="divide-y">
                        {selectedGroups.map(group => (
                          <tr key={group.key}>
                            <td className="px-3 py-2 font-mono text-xs">{group.hostLabel}</td>
                            <td className="px-3 py-2">{group.model}</td>
                            <td className="px-3 py-2">{group.version}</td>
                            <td className="px-3 py-2 font-medium">{group.total}</td>
                            <td className={cn('px-3 py-2', group.ready === group.total ? 'text-success' : 'text-destructive')}>
                              {group.ready}/{group.total}
                            </td>
                            <td className="px-2 py-2">
                              <Button type="button" variant="ghost" size="icon" title="移除此组" onClick={() => removeDeviceIds(group.ids)}>
                                <Trash2 className="h-4 w-4" />
                              </Button>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <div className="grid gap-3 md:grid-cols-3">
                    <div className="rounded-lg bg-muted/50 p-3">
                      <div className={cn('text-xs', TEXT.subtitle)}>节点数量</div>
                      <div className="mt-1 text-xl font-semibold">{readinessResult.byHost.length}</div>
                    </div>
                    <div className="rounded-lg bg-muted/50 p-3">
                      <div className={cn('text-xs', TEXT.subtitle)}>版本数量</div>
                      <div className="mt-1 text-xl font-semibold">
                        {new Set(selectedDevices.map(device => device.build_display_id).filter(Boolean)).size}
                      </div>
                    </div>
                    <div className="rounded-lg bg-muted/50 p-3">
                      <div className={cn('text-xs', TEXT.subtitle)}>型号数量</div>
                      <div className="mt-1 text-xl font-semibold">
                        {new Set(selectedDevices.map(device => device.model).filter(Boolean)).size}
                      </div>
                    </div>
                  </div>
                  {readinessResult.warnings.length > 0 ? (
                    <div className="rounded-lg bg-warning/10 px-3 py-2 text-sm text-warning">
                      {readinessResult.warnings.join('；')}
                    </div>
                  ) : (
                    <div className="rounded-lg bg-success/10 px-3 py-2 text-sm text-success">
                      版本与型号信息一致，可以继续。
                    </div>
                  )}
                </>
              )}
            </CardContent>
          </Card>
        )}

        {currentStep === 3 && (
          <Card>
            <CardHeader>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <CardTitle className="text-base">前置执行项与测试参数确认</CardTitle>
                {selectedPlanId != null && (
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={() => navigate(`/orchestration/plans/${selectedPlanId}`)}
                  >
                    编辑 Plan
                  </Button>
                )}
              </div>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-3">
                <div className="rounded-lg border p-3">
                  <div className={cn('text-xs', TEXT.subtitle)}>测试计划</div>
                  <div className="mt-1 font-medium">{selectedPlan?.name || '未选择'}</div>
                </div>
                <div className="rounded-lg border p-3">
                  <div className={cn('text-xs', TEXT.subtitle)}>执行步骤</div>
                  <div className="mt-1 font-medium">{executableStepCount} 个启用步骤</div>
                </div>
                <div className="rounded-lg border p-3">
                  <div className={cn('text-xs', TEXT.subtitle)}>测试样机</div>
                  <div className="mt-1 font-medium">{selectedDevices.length} 台 / {readinessResult.byHost.length} 节点</div>
                </div>
                <div className="rounded-lg border p-3">
                  <div className={cn('text-xs', TEXT.subtitle)}>巡检周期</div>
                  <div className="mt-1 font-medium">
                    {formatDurationSeconds(selectedPlan?.patrol_interval_seconds, 'precise', '未设置')}
                  </div>
                  <div className={cn('mt-1 text-[11px]', TEXT.subtitle)}>继承 Plan，本次不可覆盖</div>
                </div>
                <div className="rounded-lg border p-3">
                  <div className={cn('text-xs', TEXT.subtitle)}>超时</div>
                  <div className="mt-1 font-medium">
                    {formatDurationSeconds(selectedPlan?.timeout_seconds, 'precise', '未设置')}
                  </div>
                  <div className={cn('mt-1 text-[11px]', TEXT.subtitle)}>继承 Plan，本次不可覆盖</div>
                </div>
                <div className="rounded-lg border p-3">
                  <div className={cn('text-xs', TEXT.subtitle)}>失败阈值</div>
                  <div className="mt-1 font-medium">{formatFailureThreshold(selectedPlan?.failure_threshold)}</div>
                  <div className={cn('mt-1 text-[11px]', TEXT.subtitle)}>继承 Plan，本次不可覆盖</div>
                </div>
              </div>
              <div className="rounded-lg border p-3">
                <label htmlFor="plan-execute-note" className={cn('text-xs font-medium', TEXT.subtitle)}>
                  执行备注（选填）
                </label>
                <textarea
                  id="plan-execute-note"
                  value={runNote}
                  onChange={(e) => setRunNote(e.target.value.slice(0, 500))}
                  rows={2}
                  maxLength={500}
                  placeholder="记录本次发起目的、批次或关注点"
                  className="mt-2 w-full resize-y rounded-md border bg-background px-3 py-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
                />
                <div className={cn('mt-1 text-right text-[11px]', TEXT.subtitle)}>{runNote.length}/500</div>
              </div>
              <div className="rounded-lg border">
                <div className="border-b px-3 py-2 text-sm font-medium">前置执行检查</div>
                <div className="divide-y text-sm">
                  <div className="flex justify-between px-3 py-2">
                    <span>Plan 包含可执行步骤</span>
                    <span className={executableStepCount > 0 ? 'text-success' : 'text-destructive'}>
                      {executableStepCount > 0 ? '通过' : '未通过'}
                    </span>
                  </div>
                  <div className="flex justify-between px-3 py-2">
                    <span>设备与节点在线状态</span>
                    <span className={readinessResult.blockedCount === 0 && selectedDevices.length > 0 ? 'text-success' : 'text-destructive'}>
                      {readinessResult.blockedCount === 0 && selectedDevices.length > 0 ? '通过' : `${readinessResult.blockedCount} 台阻塞`}
                    </span>
                  </div>
                  <div className="flex justify-between px-3 py-2">
                    <span>版本与型号一致性</span>
                    <span className={readinessResult.warnings.length ? 'text-warning' : 'text-success'}>
                      {readinessResult.warnings.length ? '存在提醒' : '通过'}
                    </span>
                  </div>
                  <div className="flex justify-between px-3 py-2">
                    <span>所选节点当前活跃任务</span>
                    <span className={TEXT.subtitle}>{selectedHostActiveJobs} 个（信息参考，不阻塞发起）</span>
                  </div>
                </div>
              </div>
              {capacityOverflowWarnings.length > 0 && (
                <div className="space-y-1 rounded-lg bg-warning/10 px-3 py-2 text-sm text-warning">
                  {capacityOverflowWarnings.map(warning => (
                    <div key={warning.hostId}>{warning.message}</div>
                  ))}
                  <div className={cn('text-xs', TEXT.subtitle)}>心跳数据，仅供参考 · 不阻塞发起</div>
                </div>
              )}
              {readinessResult.blockedCount > 0 && (
                <Button type="button" variant="outline" onClick={() => removeDeviceIds(readinessResult.blockedDeviceIds)}>
                  <Trash2 className="mr-2 h-4 w-4" />移除全部阻塞设备
                </Button>
              )}
            </CardContent>
          </Card>
        )}

        <div className="sticky bottom-3 z-20 flex flex-col gap-3 rounded-xl border bg-background/95 p-3 shadow-lg backdrop-blur sm:flex-row sm:items-center">
          {currentStep >= 1 ? (
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
          ) : (
            <div className="flex-1" />
          )}
          {currentStep >= 1 && readinessResult.blockedCount > 0 && (
            <Button type="button" variant="outline" onClick={() => removeDeviceIds(readinessResult.blockedDeviceIds)}>
              <Trash2 className="mr-1.5 h-4 w-4" />移除阻塞设备
            </Button>
          )}
          {currentStep >= 1 && selectedDevices.length > 0 && (
            <Button type="button" variant="ghost" onClick={() => setShowClearConfirm(true)}>清空选择</Button>
          )}
          {currentStep === 0 ? (
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
            <Button type="button" variant="outline" onClick={() => handleStepChange(currentStep - 1)}>
              <ChevronLeft className="mr-1.5 h-4 w-4" />上一步
            </Button>
          )}
          {currentStep < WIZARD_STEPS.length - 1 ? (
            <Button
              type="button"
              onClick={() => handleStepChange(currentStep + 1)}
              disabled={
                (currentStep === 0 && (!selectedPlanId || executableStepCount === 0))
                || (currentStep === 1 && selectedSchedulableDeviceIds.length === 0)
              }
            >
              {currentStep === 0 ? '进入样机选择' : currentStep === 1 ? '确认节点与版本' : '进入执行前确认'}
              <ChevronRight className="ml-1.5 h-4 w-4" />
            </Button>
          ) : (
            <Button
              type="submit"
              disabled={previewing || !selectedPlanId || executableStepCount === 0 || selectedSchedulableDeviceIds.length === 0 || !readinessResult.passed}
            >
              {previewing ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Eye className="mr-2 h-4 w-4" />}
              {previewing ? '预览中...' : '预览并发起'}
            </Button>
          )}
        </div>
      </form>
    </PageContainer>
  );
}
