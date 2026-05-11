import { useEffect, useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { useToast } from '@/components/ui/toast';
import { api, type PlanRunPreview } from '@/utils/api';
import { Play, Smartphone, AlertCircle, Eye } from 'lucide-react';

type DeviceSummary = {
  id: number;
  serial: string;
  model?: string | null;
  host_id?: string | number | null;
  status: string;
};

const isSchedulable = (device: DeviceSummary) => device.status === 'ONLINE';

function DeviceRow({
  device,
  selected,
  onToggle,
}: {
  device: DeviceSummary;
  selected: boolean;
  onToggle: () => void;
}) {
  const statusColor = ({
    ONLINE: 'bg-green-400',
    OFFLINE: 'bg-gray-300',
    BUSY: 'bg-yellow-400',
  } as Record<string, string>)[device.status] ?? 'bg-gray-300';
  const disabled = !isSchedulable(device);

  return (
    <label
      className={`flex items-center gap-3 rounded-lg px-3 py-2.5 ${
        disabled ? 'cursor-not-allowed opacity-60' : 'cursor-pointer hover:bg-gray-50'
      }`}
    >
      <input type="checkbox" checked={selected} onChange={onToggle} disabled={disabled} className="rounded" />
      <span className={`w-2 h-2 rounded-full flex-shrink-0 ${statusColor}`} />
      <span className="font-mono text-sm text-gray-800">{device.serial}</span>
      {device.model && <span className="text-xs text-gray-500 flex-1 truncate">{device.model}</span>}
      <span className="text-xs text-gray-400">Host #{device.host_id}</span>
      <span className={`text-xs px-1.5 py-0.5 rounded-full ${
        device.status === 'ONLINE' ? 'bg-green-100 text-green-700' :
        device.status === 'BUSY' ? 'bg-yellow-100 text-yellow-700' : 'bg-gray-100 text-gray-500'
      }`}>{device.status}</span>
    </label>
  );
}

function PreviewDialog({
  open, preview, submitting, onClose, onConfirm,
}: {
  open: boolean; preview: PlanRunPreview | null; submitting: boolean;
  onClose: () => void; onConfirm: () => void;
}) {
  if (!open || !preview) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={onClose}>
      <div className="bg-white rounded-xl shadow-xl w-full max-w-lg mx-4 p-6" onClick={e => e.stopPropagation()}>
        <h2 className="text-lg font-semibold mb-4">确认执行</h2>
        <div className="space-y-2 text-sm">
          <div className="flex justify-between"><span className="text-gray-500">Plan</span><span className="font-medium">{preview.plan_name}</span></div>
          <div className="flex justify-between"><span className="text-gray-500">设备数</span><span className="font-medium">{preview.device_count}</span></div>
          <div className="flex justify-between"><span className="text-gray-500">Job 数</span><span className="font-medium">{preview.job_count}</span></div>
          <div className="flex justify-between"><span className="text-gray-500">总步骤数</span><span className="font-medium">{preview.total_steps}</span></div>
        </div>
        <div className="flex justify-end gap-2 mt-6">
          <Button variant="outline" onClick={onClose}>取消</Button>
          <Button onClick={onConfirm} disabled={submitting}><Play className="w-4 h-4 mr-1.5" />{submitting ? '发起中...' : '确认发起'}</Button>
        </div>
      </div>
    </div>
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

  const { data: plans, isLoading: plansLoading } = useQuery({
    queryKey: ['plans'],
    queryFn: () => api.plans.list(0, 100),
  });

  const { data: devicesResp, isLoading: devLoading } = useQuery({
    queryKey: ['devices-all'],
    queryFn: async () => { const resp = await api.devices.list(0, 200); return resp.data; },
  });

  const selectedPlan = plans?.find(p => p.id === selectedPlanId);


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
    if (selectedSchedulableDeviceIds.length === 0) { toast.error('请至少选择一台设备'); return; }

    try {
      const p = await api.plans.previewRun(selectedPlanId, {
        device_ids: selectedSchedulableDeviceIds,
      });
      setPreview(p);
      setShowPreview(true);
    } catch (err: any) {
      toast.error(err.message || '预览失败');
    }
  };

  const handleConfirm = async () => {
    if (!selectedPlanId) return;
    setSubmitting(true);
    try {
      const run = await api.plans.run(selectedPlanId, {
        device_ids: selectedSchedulableDeviceIds,
      });
      toast.success('Plan 已发起执行');
      setShowPreview(false);
      navigate(`/execution/plan-runs/${run.id}`);
    } catch (err: any) {
      toast.error(err.message || '发起失败');
    } finally {
      setSubmitting(false);
    }
  };

  const availableCount = allDevices.filter(isSchedulable).length;

  return (
    <div className="space-y-6 max-w-3xl">
      <PreviewDialog open={showPreview} preview={preview} submitting={submitting} onClose={() => setShowPreview(false)} onConfirm={handleConfirm} />

      <div>
        <h1 className="text-2xl font-semibold text-gray-900">Plan 执行</h1>
        <p className="text-gray-500 mt-1">选择已保存的 Plan 和目标设备，创建 PlanRun</p>
      </div>

      <form onSubmit={handlePreview} className="space-y-4">
        <Card>
          <CardHeader><CardTitle className="text-base">1. 选择 Plan</CardTitle></CardHeader>
          <CardContent>
            {plansLoading ? <Skeleton className="h-10 w-full" /> : (
              <select value={selectedPlanId ?? ''} onChange={e => setSelectedPlanId(e.target.value ? Number(e.target.value) : null)}
                className="w-full px-3 py-2 border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-gray-900/10 bg-white">
                <option value="">— 请选择 Plan —</option>
                {plans?.map(p => <option key={p.id} value={p.id}>{p.name}{p.steps?.length ? ` (${p.steps.length} 步骤)` : ''}</option>)}
              </select>
            )}
            {selectedPlan?.description && <p className="mt-2 text-sm text-gray-500">{selectedPlan.description}</p>}
            {selectedPlan && selectedPlan.steps?.length === 0 && (
              <div className="mt-2 flex items-center gap-2 text-sm text-amber-600 bg-amber-50 px-3 py-2 rounded-lg">
                <AlertCircle className="w-4 h-4" /> 此 Plan 没有步骤，将不会执行任何操作
              </div>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">
              <div className="flex items-center justify-between">
                <span>2. 选择设备</span>
                <span className="text-sm font-normal text-gray-500">已选 {selectedSchedulableDeviceIds.length} / {availableCount} 台可用</span>
              </div>
            </CardTitle>
          </CardHeader>
          <CardContent>
            {devLoading ? <Skeleton className="h-40 w-full" /> : allDevices.length === 0 ? (
              <div className="text-center py-6 text-gray-400">
                <Smartphone className="w-8 h-8 mx-auto mb-2 text-gray-300" /><p className="text-sm">暂无设备</p>
              </div>
            ) : (
              <>
                <div className="flex gap-2 mb-3">
                  <input type="text" placeholder="搜索设备 serial / model..." value={deviceFilter}
                    onChange={e => setDeviceFilter(e.target.value)}
                    className="flex-1 px-3 py-1.5 text-sm border rounded-lg focus:outline-none focus:ring-2 focus:ring-gray-900/10" />
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
              <p className="text-sm text-gray-600">
                <span className="font-medium text-gray-900">{Math.round((selectedPlan.failure_threshold ?? 0.05) * 100)}%</span>
                {' '}（来自 Plan 配置，执行时不可修改）
              </p>
            </CardContent>
          </Card>
        )}

        <div className="flex justify-end gap-2">
          <Button type="button" variant="outline" onClick={() => navigate(-1)}>取消</Button>
          <Button type="submit" disabled={!selectedPlanId || selectedSchedulableDeviceIds.length === 0}>
            <Eye className="w-4 h-4 mr-2" />预览并发起
          </Button>
        </div>
      </form>
    </div>
  );
}
