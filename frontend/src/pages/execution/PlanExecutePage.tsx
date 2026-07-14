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



import { api, ApiError, type PlanRunPreview } from '@/utils/api';



import { planKeys } from '@/utils/api/queryKeys';



import { Play, Smartphone, AlertCircle, Eye, ExternalLink, RefreshCw } from 'lucide-react';



import { PageContainer, PageHeader } from '@/components/layout';
import { STATUS_BG_COLORS } from '@/design-system/colors';
import { DEVICE_STATUS_DOT, TEXT } from '@/design-system/tokens';
import { cn } from '@/lib/utils';

import { EmptyState } from '@/components/ui/empty-state';
import { ErrorState } from '@/components/ui/error-state';







type DeviceSummary = {



  id: number;



  serial: string;



  model?: string | null;



  host_id?: string | number | null;



  status: string;
  schedulable?: boolean;
  scheduling_reason?: string | null;



};







const isSchedulable = (device: DeviceSummary) =>
  typeof device.schedulable === 'boolean'
    ? device.schedulable
    : device.status === 'ONLINE';







function DeviceRow({



  device,



  selected,



  onToggle,



}: {



  device: DeviceSummary;



  selected: boolean;



  onToggle: () => void;



}) {



  const statusColor =
    DEVICE_STATUS_DOT[device.status as keyof typeof DEVICE_STATUS_DOT] ?? DEVICE_STATUS_DOT.OFFLINE;



  const disabled = !isSchedulable(device);







  return (



    <label



      className={cn(
        'flex items-center gap-3 rounded-lg px-3 py-2.5',
        disabled ? 'cursor-not-allowed opacity-60' : 'cursor-pointer hover:bg-accent',
      )}



    >



      <input type="checkbox" checked={selected} onChange={onToggle} disabled={disabled} className="rounded" />



      <span className={`w-2 h-2 rounded-full flex-shrink-0 ${statusColor}`} />



      <span className={cn('font-mono text-sm', TEXT.body)}>{device.serial}</span>



      {device.model && <span className={cn('text-xs flex-1 truncate', TEXT.subtitle)}>{device.model}</span>}



      <span className={cn('text-xs', TEXT.subtitle)}>Host #{device.host_id}</span>



      <StatusBadge kind="device" status={device.status} size="sm" />



    </label>



  );



}







function PreviewDialog({



  open, preview, submitting, onClose, onConfirm,



}: {



  open: boolean; preview: PlanRunPreview | null; submitting: boolean;



  onClose: () => void; onConfirm: () => void;



}) {



  return (



    <Dialog open={open && preview != null} onOpenChange={(o) => { if (!o) onClose(); }}>



      <DialogContent>



        <DialogHeader>



          <DialogTitle>确认执行</DialogTitle>



          <DialogDescription>{preview?.plan_name}</DialogDescription>



        </DialogHeader>



        <div className="space-y-2 text-sm">



          <div className="flex justify-between"><span className="text-muted-foreground">设备数</span><span className="font-medium">{preview?.device_count ?? '—'}</span></div>



          <div className="flex justify-between"><span className="text-muted-foreground">Job 数</span><span className="font-medium">{preview?.job_count ?? '—'}</span></div>



          <div className="flex justify-between"><span className="text-muted-foreground">总步骤数</span><span className="font-medium">{preview?.total_steps ?? '—'}</span></div>



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



  const [deviceFilter, setDeviceFilter] = useState('');



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



  const {
    data: devicesResp,
    isLoading: devLoading,
    isError: devicesError,
    error: devicesQueryError,
    refetch: refetchDevices,
  } = useQuery({



    queryKey: ['devices-all'],



    queryFn: async () => { const resp = await api.devices.list(0, 200); return resp; },



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



  const filteredDevices = allDevices.filter(d =>



    !deviceFilter || d.serial.includes(deviceFilter) ||



    (d.model ?? '').toLowerCase().includes(deviceFilter.toLowerCase())



  );







  useEffect(() => {



    setSelectedDeviceIds(prev => {



      const next = new Set(Array.from(prev).filter(id => schedulableDeviceIds.has(id)));



      return next.size === prev.size ? prev : next;



    });



  }, [schedulableDeviceIds]);







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







  const handlePreview = async (e: React.FormEvent) => {



    e.preventDefault();



    if (!selectedPlanId) { toast.error('请选择 Plan'); return; }
    if (!selectedPlan || executableStepCount === 0) {
      toast.error('Plan 至少需要一个已启用步骤才能执行');
      return;
    }



    if (selectedSchedulableDeviceIds.length === 0) { toast.error('请至少选择一台设备'); return; }







    try {



      const frozenDeviceIds = [...selectedSchedulableDeviceIds];
      const p = await api.plans.previewRun(selectedPlanId, {



        device_ids: frozenDeviceIds,



      });



      if (p.total_steps === 0) {
        toast.error('Plan 没有可执行步骤，无法发起');
        return;
      }
      setPreview({
        ...p,
        device_ids: p.device_ids?.length ? [...p.device_ids] : frozenDeviceIds,
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







  const availableCount = allDevices.filter(isSchedulable).length;







  return (



    <PageContainer width="narrow">



      <PreviewDialog open={showPreview} preview={preview} submitting={submitting} onClose={() => setShowPreview(false)} onConfirm={handleConfirm} />



      <PageHeader title="Plan 执行" subtitle="选择已保存的 Plan 和目标设备，创建 PlanRun" />

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



        <Card>



          <CardHeader><CardTitle className="text-base">1. 选择 Plan</CardTitle></CardHeader>



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



            {selectedPlan && executableStepCount === 0 && (



              <div className={`mt-2 flex items-center gap-2 text-sm ${STATUS_BG_COLORS.warning} px-3 py-2 rounded-lg`}>



                <AlertCircle className="w-4 h-4" /> 此 Plan 没有已启用步骤，无法执行



              </div>



            )}



          </CardContent>



        </Card>







        <Card>



          <CardHeader>



            <CardTitle className="text-base">



              <div className="flex items-center justify-between">



                <span>2. 选择设备</span>



                <span className={cn('text-sm font-normal', TEXT.subtitle)}>已选 {selectedSchedulableDeviceIds.length} / {availableCount} 台可用</span>



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



                <div className="flex gap-2 mb-3">



                  <Input type="text" placeholder="搜索设备 serial / model..." value={deviceFilter}



                    onChange={e => setDeviceFilter(e.target.value)}

                  />



                  <Button type="button" variant="outline" size="sm" onClick={toggleAll}>全选/取消</Button>



                </div>



                <div className="max-h-60 overflow-y-auto border rounded-lg divide-y">



                  {filteredDevices.map((d: DeviceSummary) => (



                    <DeviceRow key={d.id} device={d} selected={selectedDeviceIds.has(d.id)} onToggle={() => toggleDevice(d)} />



                  ))}



                </div>



              </>



            )}



          </CardContent>



        </Card>







        {selectedPlan && (



          <Card>



            <CardHeader><CardTitle className="text-base">3. 失败阈值</CardTitle></CardHeader>



            <CardContent>



              <p className={cn('text-sm', TEXT.body)}>



                <span className={cn('font-medium', TEXT.heading)}>{Math.round((selectedPlan.failure_threshold ?? 0.05) * 100)}%</span>



                {' '}（来自 Plan 配置，执行时不可修改）



              </p>



            </CardContent>



          </Card>



        )}







        <div className="flex justify-end gap-2">



          <Button type="button" variant="outline" onClick={() => navigate(-1)}>取消</Button>



          <Button
            type="submit"
            disabled={!selectedPlanId || executableStepCount === 0 || selectedSchedulableDeviceIds.length === 0}
          >



            <Eye className="w-4 h-4 mr-2" />预览并发起



          </Button>



        </div>



      </form>



    </PageContainer>



  );



}



