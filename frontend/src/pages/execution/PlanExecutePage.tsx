import { useEffect, useMemo, useState } from 'react';



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



import { useToast } from '@/hooks/useToast';



import { api, ApiError, fetchHostList, type PlanRunPreview } from '@/utils/api';



import { hostKeys, planKeys } from '@/utils/api/queryKeys';



import { Play, Smartphone, AlertCircle, Eye, ExternalLink, RefreshCw, Layers3, Trash2, ChevronLeft, ChevronRight } from 'lucide-react';



import { PageContainer, PageHeader } from '@/components/layout';
import { STATUS_BG_COLORS } from '@/design-system/colors';
import { TEXT } from '@/design-system/tokens';
import { cn } from '@/lib/utils';

import { EmptyState } from '@/components/ui/empty-state';
import { ErrorState } from '@/components/ui/error-state';
import { evaluateDeviceReadiness } from '@/utils/planExecuteReadiness';
import { PlanExecuteWizardNav, WIZARD_STEPS } from '@/components/execution/PlanExecuteWizardNav';
import { PlanConfigStep, DeviceSelectionStep, VersionConfirmStep, ExecutionConfirmStep } from '@/components/execution/PlanExecuteSteps';







type DeviceSummary = {



  id: number;



  serial: string;



  model?: string | null;



  host_id?: string | number | null;



  status: string;
  schedulable?: boolean;
  scheduling_reason?: string | null;
  adb_connected?: boolean | null;
  adb_state?: string | null;
  build_display_id?: string | null;



};







const isSchedulable = (device: DeviceSummary) =>
  typeof device.schedulable === 'boolean'
    ? device.schedulable
    : device.status === 'ONLINE';








function PreviewDialog({



  open, preview, submitting, failureThreshold, groups, devices, onClose, onConfirm,



}: {



  open: boolean; preview: PlanRunPreview | null; submitting: boolean;
  failureThreshold: number;
  groups: Array<{ key: string; hostLabel: string; model: string; version: string; total: number; ready: number }>;
  devices: DeviceSummary[];



  onClose: () => void; onConfirm: () => void;



}) {



  return (



    <Dialog open={open && preview != null} onOpenChange={(o) => { if (!o) onClose(); }}>



      <DialogContent>



        <DialogHeader>



          <DialogTitle>确认执行</DialogTitle>



          <DialogDescription>{preview?.plan_name}</DialogDescription>



        </DialogHeader>



        <div className="space-y-3 text-sm">



          <div className="flex justify-between"><span className="text-muted-foreground">设备数</span><span className="font-medium">{preview?.device_count ?? '—'}</span></div>



          <div className="flex justify-between"><span className="text-muted-foreground">Job 数</span><span className="font-medium">{preview?.job_count ?? '—'}</span></div>



          <div className="flex justify-between"><span className="text-muted-foreground">总步骤数</span><span className="font-medium">{preview?.total_steps ?? '—'}</span></div>
          <div className="flex justify-between"><span className="text-muted-foreground">失败阈值</span><span className="font-medium">{Math.round(failureThreshold * 100)}%</span></div>
          <div className="rounded-lg border"><div className="border-b px-3 py-2 font-medium">节点 / 型号 / 版本分布</div><div className="max-h-40 divide-y overflow-auto">{groups.map(group => <div key={group.key} className="grid grid-cols-[1fr_1fr_1fr_auto] gap-2 px-3 py-2 text-xs"><span>{group.hostLabel}</span><span>{group.model}</span><span className="truncate" title={group.version}>{group.version}</span><span>{group.total} 台</span></div>)}</div></div>
          <details className="rounded-lg border"><summary className="cursor-pointer px-3 py-2 font-medium">查看设备 Serial（{devices.length}）</summary><div className="max-h-32 overflow-auto border-t p-3 font-mono text-xs leading-6">{devices.map(device => <div key={device.id}>{device.serial}</div>)}</div></details>



        </div>



        <DialogFooter>



          <Button variant="outline" onClick={onClose}>取消</Button>



          <Button onClick={onConfirm} disabled={submitting}><Play className="w-4 h-4 mr-1.5" />{submitting ? '发起中...' : '确认发起'}</Button>



        </DialogFooter>



      </DialogContent>



    </Dialog>



  );



}







export default function PlanExecutePage() {




  const navigate = useNavigate();



  const toast = useToast();



  const [searchParams] = useSearchParams();



  const [selectedPlanId, setSelectedPlanId] = useState<number | null>(



    searchParams.get('plan') ? Number(searchParams.get('plan')) : null



  );



  const [selectedDeviceIds, setSelectedDeviceIds] = useState<Set<number>>(new Set());
  const [currentStep, setCurrentStep] = useState(0);
  const wizardSteps = WIZARD_STEPS;
  const [nodeSearch, setNodeSearch] = useState('');



  const [deviceFilter, setDeviceFilter] = useState('');
  const [deviceVersionFilter, setDeviceVersionFilter] = useState('all');
  const [deviceHostFilter, setDeviceHostFilter] = useState('all');
  const [deviceModelFilter, setDeviceModelFilter] = useState('all');



  const [preview, setPreview] = useState<PlanRunPreview | null>(null);



  const [showPreview, setShowPreview] = useState(false);



  const [submitting, setSubmitting] = useState(false);
  const [retryingDispatch, setRetryingDispatch] = useState(false);
  const [dispatchFailure, setDispatchFailure] = useState<{
    planRunId: number;
    message: string;
    retryable: boolean;
  } | null>(null);







  const {
    data: plans,
    isLoading: plansLoading,
    isError: plansError,
    error: plansQueryError,
    refetch: refetchPlans,
  } = useQuery({



    queryKey: planKeys.list(100),

    queryFn: () => api.plans.list(0, 100),

  });

  const { data: hostsList } = useQuery({
    queryKey: hostKeys.list(),
    queryFn: () => fetchHostList(0, 200),
  });



  const {
    data: devicesResp,
    isLoading: devLoading,
    isError: devicesError,
    error: devicesQueryError,
    refetch: refetchDevices,
  } = useQuery({



    queryKey: ['devices-all'],



    queryFn: async () => { const resp = await api.devices.list(0, 200); return resp; },
    refetchInterval: 20_000,



  });







  const selectedPlan = plans?.find(p => p.id === selectedPlanId);
  const executableStepCount =
    selectedPlan?.steps?.filter((step) => step.enabled !== false).length ?? 0;











  const allDevices = devicesResp?.items ?? [];



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
  const versionOptions = useMemo(() => Array.from(new Set(allDevices.map((device: DeviceSummary) => device.build_display_id).filter(Boolean) as string[])).sort(), [allDevices]);
  const modelOptions = useMemo(() => Array.from(new Set(allDevices.map((device: DeviceSummary) => device.model).filter(Boolean) as string[])).sort(), [allDevices]);
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
    return { id, label, total: devices.length, selected, available, online: !host || host.status === 'ONLINE' };
  }), [allDevices, hostMap, hostOptions, selectedDeviceIds]);
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



  const filteredDevices = allDevices.filter(d =>



    !deviceFilter || d.serial.includes(deviceFilter) ||



    (d.model ?? '').toLowerCase().includes(deviceFilter.toLowerCase())

  ).filter((d: DeviceSummary) =>
    (deviceVersionFilter === 'all' || d.build_display_id === deviceVersionFilter) &&
    (deviceHostFilter === 'all' || String(d.host_id ?? 'unassigned') === deviceHostFilter) &&
    (deviceModelFilter === 'all' || d.model === deviceModelFilter)



  );
  const filteredAvailableIds = filteredDevices.filter(isSchedulable).map((device: DeviceSummary) => device.id);
  const allFilteredSelected = filteredAvailableIds.length > 0 && filteredAvailableIds.every(id => selectedDeviceIds.has(id));







  useEffect(() => {



    setSelectedDeviceIds(prev => {
      const next = new Set(Array.from(prev).filter(id => schedulableDeviceIds.has(id)));
      const removedCount = prev.size - next.size;
      if (removedCount > 0) toast.info(`${removedCount} 台样机状态已变化，已从本次执行中移除`);
      return next.size === prev.size ? prev : next;



    });



  }, [schedulableDeviceIds, toast]);







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



      setSelectedDeviceIds(prev => { const next = new Set(prev); available.forEach(id => next.delete(id)); return next; });



    } else {



      setSelectedDeviceIds(prev => { const next = new Set(prev); available.forEach(id => next.add(id)); return next; });



    }



  };
  const removeDeviceIds = (ids: number[]) => setSelectedDeviceIds(prev => {
    const next = new Set(prev);
    ids.forEach(id => next.delete(id));
    return next;
  });







  const handlePreview = async (e: React.FormEvent) => {



    e.preventDefault();



    if (!selectedPlanId) { toast.error('请选择 Plan'); return; }
    if (!selectedPlan || executableStepCount === 0) {
      toast.error('Plan 至少需要一个已启用步骤才能执行');
      return;
    }



    if (selectedSchedulableDeviceIds.length === 0) { toast.error('请至少选择一台设备'); return; }
    if (!readinessResult.passed) { toast.error('测试准备检查未通过'); return; }







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



    }



  };







  const handleConfirm = async () => {



    if (!selectedPlanId || !preview || preview.total_steps === 0) return;



    setSubmitting(true);



    try {



      const run = await api.plans.run(selectedPlanId, {



        device_ids: [...preview.device_ids],



      });



      toast.success('Plan 已发起执行');



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







  return (



    <PageContainer width="wide">



      <PreviewDialog open={showPreview} preview={preview} submitting={submitting} failureThreshold={selectedPlan?.failure_threshold ?? 0.05} groups={selectedGroups} devices={selectedDevices} onClose={() => setShowPreview(false)} onConfirm={handleConfirm} />



      <PageHeader title="Plan 执行" subtitle="选择已保存的 Plan 和目标设备，创建 PlanRun" />

      <PlanExecuteWizardNav currentStep={currentStep} onStepChange={handleStepChange} />


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



        {currentStep === 0 && <PlanConfigStep><Card>



          <CardHeader><CardTitle className="text-base">Plan 配置</CardTitle></CardHeader>



          <CardContent>



            {plansLoading ? <Skeleton className="h-10 w-full" /> : plansError ? (
              <ErrorState
                title="加载 Plan 失败"
                description={(plansQueryError as Error)?.message || '请检查网络连接或稍后重试'}
                onRetry={() => void refetchPlans()}
              />
            ) : (



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



                  {plans?.map(p => (
                    <SelectItem key={p.id} value={String(p.id)}>
                      {p.name}{p.steps?.length ? ` (${p.steps.length} 步骤)` : ''}
                    </SelectItem>
                  ))}



                </SelectContent>



              </Select>



            )}



            {selectedPlan?.description && <p className={cn('mt-2 text-sm', TEXT.subtitle)}>{selectedPlan.description}</p>}

            {selectedPlan && (
              <div className="mt-4 grid gap-4 lg:grid-cols-[220px_1fr]">
                <div className="grid grid-cols-2 gap-2 lg:grid-cols-1">
                  <div className="rounded-lg bg-muted/50 p-3"><div className={cn('text-xs', TEXT.subtitle)}>失败阈值</div><div className="mt-1 font-semibold">{Math.round((selectedPlan.failure_threshold ?? 0.05) * 100)}%</div></div>
                  <div className="rounded-lg bg-muted/50 p-3"><div className={cn('text-xs', TEXT.subtitle)}>启用步骤</div><div className="mt-1 font-semibold">{executableStepCount} / {selectedPlan.steps?.length ?? 0}</div></div>
                </div>
                <div className="rounded-lg border">
                  <div className="border-b px-3 py-2 text-sm font-medium">执行步骤</div>
                  <div className="divide-y">{selectedPlan.steps?.map((step, index) => <div key={step.id ?? step.step_key} className={cn('grid grid-cols-[32px_80px_1fr_auto] items-center gap-2 px-3 py-2 text-xs', step.enabled === false && 'opacity-50')}><span>{index + 1}</span><span>{step.stage}</span><span className="truncate">{step.script_name} · {step.script_version}</span><span>{step.enabled === false ? '停用' : '启用'}</span></div>)}</div>
                </div>
              </div>
            )}
            {selectedPlan && <div className={cn('mt-3 flex flex-wrap gap-x-5 gap-y-1 text-xs', TEXT.subtitle)}><span>更新时间：{selectedPlan.updated_at ? new Date(selectedPlan.updated_at).toLocaleString() : '暂无记录'}</span><span>巡检周期：{selectedPlan.patrol_interval_seconds ? `${selectedPlan.patrol_interval_seconds}s` : '未设置'}</span><span>超时：{selectedPlan.timeout_seconds ? `${selectedPlan.timeout_seconds}s` : '未设置'}</span></div>}



            {selectedPlan && executableStepCount === 0 && (



              <div className={`mt-2 flex items-center gap-2 text-sm ${STATUS_BG_COLORS.warning} px-3 py-2 rounded-lg`}>



                <AlertCircle className="w-4 h-4" /> 此 Plan 没有已启用步骤，无法执行



              </div>



            )}



          </CardContent>



        </Card></PlanConfigStep>}

        {currentStep === 1 && <DeviceSelectionStep><Card>



          <CardHeader>



            <CardTitle className="text-base">



              <div className="flex items-center justify-between">



                <span>设备编排</span>



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



              <>

                <div className="grid gap-4 lg:grid-cols-[260px_minmax(0,1fr)]">
                  <aside className="rounded-lg border bg-muted/20 p-2">
                    <div className="px-2 py-2 text-sm font-medium">搜索并选择节点</div><div className={cn('px-2 pb-2 text-xs', TEXT.subtitle)}>已选 {selectedSchedulableDeviceIds.length} / {schedulableDeviceIds.size} 台可用</div>
                    <Input className="mb-2" value={nodeSearch} onChange={event => setNodeSearch(event.target.value)} placeholder="节点 IP / 名称" autoFocus />
                    <div className="space-y-1">{visibleNodeSummaries.map(node => <button key={node.id} type="button" onClick={() => setDeviceHostFilter(node.id)} className={cn('w-full rounded-lg border px-3 py-2 text-left transition-colors', deviceHostFilter === node.id ? 'border-primary bg-primary/10' : 'border-transparent hover:bg-accent')}><div className="flex items-center justify-between gap-2"><span className="truncate font-mono text-xs">{node.label}</span><span className={cn('h-2 w-2 rounded-full', node.online ? 'bg-success' : 'bg-destructive')} /></div><div className={cn('mt-1 flex justify-between text-xs', TEXT.subtitle)}><span>{node.total} 台 · {node.available} 可用</span><span>{node.selected} 已选</span></div><div className="mt-2 h-1 overflow-hidden rounded bg-muted"><div className="h-full bg-primary" style={{ width: `${node.total ? node.selected / node.total * 100 : 0}%` }} /></div></button>)}</div>
                    {visibleNodeSummaries.length === 0 && <div className={cn('px-2 py-6 text-center text-xs', TEXT.subtitle)}>未找到匹配节点</div>}
                  </aside>
                  <section className="min-w-0">
                    {deviceHostFilter === 'all' ? <div className={cn('flex min-h-72 flex-col items-center justify-center rounded-lg border border-dashed text-sm', TEXT.subtitle)}><Layers3 className="mb-3 h-8 w-8" /><span>请先从左侧选择一个节点</span><span className="mt-1 text-xs">再查看该节点的设备矩阵并选择设备</span></div> : <>
                      <div className="mb-3 flex flex-wrap items-center gap-2">
                        <Input className="min-w-48 flex-1" type="text" placeholder="搜索当前节点的 Serial / 型号" value={deviceFilter} onChange={event => setDeviceFilter(event.target.value)} />
                        <Select value={deviceVersionFilter} onValueChange={setDeviceVersionFilter}><SelectTrigger className="w-44"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="all">全部版本</SelectItem>{versionOptions.map(value => <SelectItem key={value} value={value}>{value}</SelectItem>)}</SelectContent></Select>
                        <Select value={deviceModelFilter} onValueChange={setDeviceModelFilter}><SelectTrigger className="w-40"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="all">全部型号</SelectItem>{modelOptions.map(value => <SelectItem key={value} value={value}>{value}</SelectItem>)}</SelectContent></Select>
                        <Button type="button" variant="outline" onClick={toggleAll}>{allFilteredSelected ? '取消选择当前结果' : '全选当前结果'}</Button>
                      </div>
                      <div className="mb-3 rounded-lg border p-3">
                        <div className="mb-2 flex flex-wrap items-center justify-between gap-2"><div><div className="text-sm font-medium">已选样机 Minimap</div><div className={cn('text-xs', TEXT.subtitle)}>跨节点汇总本次已选的 {selectedDevices.length} 台样机 · 点击方块可从本次测试中移除</div></div><div className="flex gap-3 text-xs"><span><i className="mr-1 inline-block h-2.5 w-2.5 rounded-sm bg-success" />已选就绪</span><span><i className="mr-1 inline-block h-2.5 w-2.5 rounded-sm bg-destructive" />已选阻塞</span></div></div>
                        {selectedDevices.length === 0 ? <div className={cn('flex min-h-20 items-center justify-center text-xs', TEXT.subtitle)}>尚未选择样机</div> : <div className="grid gap-1" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(28px, 1fr))' }}>{selectedDevices.map((device: DeviceSummary) => { const row = readinessResult.rows.find(item => item.device.id === device.id); const blocked = row && !row.ready; const host = hostMap.get(String(device.host_id)); return <button key={device.id} type="button" aria-label={`已选设备方块 ${device.id}`} title={`${device.serial} · ${host?.ip || host?.name || device.host_id || '节点未知'} · ${device.model || '型号未知'} · ${device.build_display_id || '版本未知'}`} onClick={() => toggleDevice(device)} className={cn('aspect-square rounded-sm border transition-transform hover:scale-110 hover:ring-2 hover:ring-primary/40', blocked ? 'border-destructive bg-destructive' : 'border-success bg-success')} />; })}</div>}
                      </div>
                      <div className="overflow-x-auto rounded-lg border"><table className="w-full min-w-[680px] text-sm"><thead className="bg-muted/95 text-left text-xs"><tr><th className="w-10 px-3 py-2" /><th className="px-3 py-2">Serial</th><th className="px-3 py-2">型号</th><th className="px-3 py-2">版本</th><th className="px-3 py-2">状态</th><th className="px-3 py-2">预检</th></tr></thead><tbody className="divide-y">{filteredDevices.map((device: DeviceSummary) => { const disabled = !isSchedulable(device); const row = readinessResult.rows.find(item => item.device.id === device.id); return <tr key={device.id} className={cn(disabled ? 'opacity-50' : 'cursor-pointer hover:bg-accent/50')} onClick={() => toggleDevice(device)}><td className="px-3 py-2"><input aria-label={`选择 ${device.serial}`} type="checkbox" checked={selectedDeviceIds.has(device.id)} disabled={disabled} readOnly /></td><td className="px-3 py-2 font-mono text-xs">{device.serial}</td><td className="px-3 py-2">{device.model || '—'}</td><td className="px-3 py-2">{device.build_display_id || '—'}</td><td className="px-3 py-2"><StatusBadge kind="device" status={device.status} size="sm" /></td><td className={cn('px-3 py-2 text-xs', row?.ready ? 'text-success' : row ? 'text-destructive' : TEXT.subtitle)}>{row?.ready ? '就绪' : row ? row.reasons.join('、') : '选择后检查'}</td></tr>; })}</tbody></table></div>
                    </>}
                  </section>
                </div>



              </>



            )}



          </CardContent>



        </Card></DeviceSelectionStep>}

        {currentStep === 2 && <VersionConfirmStep><Card>
          <CardHeader><CardTitle className="text-base">节点数量与版本一致性确认</CardTitle></CardHeader>
          <CardContent className="space-y-4">
            {selectedGroups.length === 0 ? <EmptyState title="尚未选择样机" description="请返回“样机选择”，按节点选择测试设备" icon={<Smartphone className="h-10 w-10" />} /> : <>
              <div className="overflow-x-auto rounded-lg border"><table className="w-full min-w-[720px] text-sm"><thead className="bg-muted/70 text-left text-xs"><tr><th className="px-3 py-2">节点 IP</th><th className="px-3 py-2">型号</th><th className="px-3 py-2">版本</th><th className="px-3 py-2">选择数量</th><th className="px-3 py-2">就绪</th><th className="w-10 px-2 py-2" /></tr></thead><tbody className="divide-y">{selectedGroups.map(group => <tr key={group.key}><td className="px-3 py-2 font-mono text-xs">{group.hostLabel}</td><td className="px-3 py-2">{group.model}</td><td className="px-3 py-2">{group.version}</td><td className="px-3 py-2 font-medium">{group.total}</td><td className={cn('px-3 py-2', group.ready === group.total ? 'text-success' : 'text-destructive')}>{group.ready}/{group.total}</td><td className="px-2 py-2"><Button type="button" variant="ghost" size="icon" title="移除此组" onClick={() => removeDeviceIds(group.ids)}><Trash2 className="h-4 w-4" /></Button></td></tr>)}</tbody></table></div>
              <div className="grid gap-3 md:grid-cols-3"><div className="rounded-lg bg-muted/50 p-3"><div className={cn('text-xs', TEXT.subtitle)}>节点数量</div><div className="mt-1 text-xl font-semibold">{readinessResult.byHost.length}</div></div><div className="rounded-lg bg-muted/50 p-3"><div className={cn('text-xs', TEXT.subtitle)}>版本数量</div><div className="mt-1 text-xl font-semibold">{new Set(selectedDevices.map(device => device.build_display_id).filter(Boolean)).size}</div></div><div className="rounded-lg bg-muted/50 p-3"><div className={cn('text-xs', TEXT.subtitle)}>型号数量</div><div className="mt-1 text-xl font-semibold">{new Set(selectedDevices.map(device => device.model).filter(Boolean)).size}</div></div></div>
              {readinessResult.warnings.length > 0 ? <div className="rounded-lg bg-warning/10 px-3 py-2 text-sm text-warning">{readinessResult.warnings.join('；')}</div> : <div className="rounded-lg bg-success/10 px-3 py-2 text-sm text-success">版本与型号信息一致，可以继续。</div>}
            </>}
          </CardContent>
        </Card></VersionConfirmStep>}

        {currentStep === 3 && <ExecutionConfirmStep><Card>
          <CardHeader><CardTitle className="text-base">前置执行项与测试参数确认</CardTitle></CardHeader>
          <CardContent className="space-y-4">
            <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-4"><div className="rounded-lg border p-3"><div className={cn('text-xs', TEXT.subtitle)}>测试计划</div><div className="mt-1 font-medium">{selectedPlan?.name || '未选择'}</div></div><div className="rounded-lg border p-3"><div className={cn('text-xs', TEXT.subtitle)}>执行步骤</div><div className="mt-1 font-medium">{executableStepCount} 个启用步骤</div></div><div className="rounded-lg border p-3"><div className={cn('text-xs', TEXT.subtitle)}>失败阈值</div><div className="mt-1 font-medium">{Math.round((selectedPlan?.failure_threshold ?? 0.05) * 100)}%</div></div><div className="rounded-lg border p-3"><div className={cn('text-xs', TEXT.subtitle)}>测试设备</div><div className="mt-1 font-medium">{selectedDevices.length} 台 / {readinessResult.byHost.length} 节点</div></div></div>
            <div className="rounded-lg border"><div className="border-b px-3 py-2 text-sm font-medium">前置执行检查</div><div className="divide-y text-sm"><div className="flex justify-between px-3 py-2"><span>Plan 包含可执行步骤</span><span className={executableStepCount > 0 ? 'text-success' : 'text-destructive'}>{executableStepCount > 0 ? '通过' : '未通过'}</span></div><div className="flex justify-between px-3 py-2"><span>设备与节点在线状态</span><span className={readinessResult.blockedCount === 0 && selectedDevices.length > 0 ? 'text-success' : 'text-destructive'}>{readinessResult.blockedCount === 0 && selectedDevices.length > 0 ? '通过' : `${readinessResult.blockedCount} 台阻塞`}</span></div><div className="flex justify-between px-3 py-2"><span>版本与型号一致性</span><span className={readinessResult.warnings.length ? 'text-warning' : 'text-success'}>{readinessResult.warnings.length ? '存在提醒' : '通过'}</span></div></div></div>
            {readinessResult.blockedCount > 0 && <Button type="button" variant="outline" onClick={() => removeDeviceIds(readinessResult.blockedDeviceIds)}><Trash2 className="mr-2 h-4 w-4" />移除全部阻塞设备</Button>}
          </CardContent>
        </Card></ExecutionConfirmStep>}

        <div className="sticky bottom-3 z-20 flex flex-col gap-3 rounded-xl border bg-background/95 p-3 shadow-lg backdrop-blur sm:flex-row sm:items-center">
          <div className="flex-1 text-sm"><span className="font-medium">已选 {selectedDevices.length} 台</span><span className="mx-2 text-muted-foreground">|</span><span className="text-success">{readinessResult.readyCount} 台就绪</span><span className="mx-2 text-muted-foreground">|</span><span className={readinessResult.blockedCount ? 'text-destructive' : TEXT.subtitle}>{readinessResult.blockedCount} 台阻塞</span></div>
          {readinessResult.blockedCount > 0 && <Button type="button" variant="outline" onClick={() => removeDeviceIds(readinessResult.blockedDeviceIds)}><Trash2 className="mr-1.5 h-4 w-4" />移除阻塞设备</Button>}
          {selectedDevices.length > 0 && <Button type="button" variant="ghost" onClick={() => setSelectedDeviceIds(new Set())}>清空选择</Button>}
          {currentStep === 0 ? <Button type="button" variant="outline" onClick={() => navigate('/orchestration/plans')}>取消</Button> : <Button type="button" variant="outline" onClick={() => setCurrentStep(step => Math.max(0, step - 1))}><ChevronLeft className="mr-1.5 h-4 w-4" />上一步</Button>}
          {currentStep < wizardSteps.length - 1 ? <Button type="button" onClick={() => setCurrentStep(step => Math.min(wizardSteps.length - 1, step + 1))} disabled={(currentStep === 0 && (!selectedPlanId || executableStepCount === 0)) || (currentStep === 1 && selectedSchedulableDeviceIds.length === 0)}>{currentStep === 0 ? '进入样机选择' : currentStep === 1 ? '确认节点与版本' : '进入执行前确认'}<ChevronRight className="ml-1.5 h-4 w-4" /></Button> : <Button type="submit" disabled={!selectedPlanId || executableStepCount === 0 || selectedSchedulableDeviceIds.length === 0 || !readinessResult.passed}><Eye className="mr-2 h-4 w-4" />预览并发起</Button>}



        </div>



      </form>



    </PageContainer>



  );



}
