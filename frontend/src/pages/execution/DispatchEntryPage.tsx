import { useState, useEffect } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { api, type WorkflowDefinition, type Device } from '@/utils/api';
import { useToast } from '@/components/ui/toast';
import { Play, Smartphone, AlertCircle } from 'lucide-react';
import DispatchPreviewDialog from '@/pages/orchestration/DispatchPreviewDialog';

function DeviceRow({
  device,
  selected,
  onToggle,
}: {
  device: Device;
  selected: boolean;
  onToggle: () => void;
}) {
  const statusColor = {
    ONLINE: 'bg-green-400',
    OFFLINE: 'bg-gray-300',
    BUSY: 'bg-yellow-400',
  }[device.status] ?? 'bg-gray-300';

  return (
    <label className="flex items-center gap-3 px-3 py-2.5 rounded-lg hover:bg-gray-50 cursor-pointer">
      <input
        type="checkbox"
        checked={selected}
        onChange={onToggle}
        disabled={device.status === 'OFFLINE'}
        className="rounded"
      />
      <span className={`w-2 h-2 rounded-full flex-shrink-0 ${statusColor}`} />
      <span className="font-mono text-sm text-gray-800">{device.serial}</span>
      {device.model && (
        <span className="text-xs text-gray-500 flex-1 truncate">{device.model}</span>
      )}
      <span className="text-xs text-gray-400">Host #{device.host_id}</span>
      <span className={`text-xs px-1.5 py-0.5 rounded-full ${
        device.status === 'ONLINE' ? 'bg-green-100 text-green-700' :
        device.status === 'BUSY'   ? 'bg-yellow-100 text-yellow-700' :
                                     'bg-gray-100 text-gray-500'
      }`}>{device.status}</span>
    </label>
  );
}

export default function DispatchEntryPage() {
  const navigate = useNavigate();
  const toast = useToast();
  const [searchParams] = useSearchParams();
  const [selectedWorkflowId, setSelectedWorkflowId] = useState<number | null>(
    searchParams.get('workflow') ? Number(searchParams.get('workflow')) : null
  );
  const [selectedDeviceIds, setSelectedDeviceIds] = useState<Set<number>>(new Set());
  const [failureThreshold, setFailureThreshold] = useState<number>(0.05);
  const [showPreview, setShowPreview] = useState(false);
  const [deviceFilter, setDeviceFilter] = useState('');

  const { data: workflows, isLoading: wfLoading } = useQuery({
    queryKey: ['workflow-definitions'],
    queryFn: () => api.orchestration.list(0, 100),
  });

  const { data: devicesResp, isLoading: devLoading } = useQuery({
    queryKey: ['devices-all'],
    queryFn: async () => {
      const resp = await api.devices.list(0, 200);
      return resp.data;
    },
  });

  const selectedWf = workflows?.find(w => w.id === selectedWorkflowId) as WorkflowDefinition | undefined;

  // Sync threshold from selected workflow
  useEffect(() => {
    if (selectedWf) setFailureThreshold(selectedWf.failure_threshold);
  }, [selectedWf?.id]);

  const allDevices = devicesResp?.items ?? [];
  const filteredDevices = allDevices.filter(d =>
    !deviceFilter ||
    d.serial.includes(deviceFilter) ||
    (d.model ?? '').toLowerCase().includes(deviceFilter.toLowerCase())
  );

  const toggleDevice = (id: number) => {
    setSelectedDeviceIds(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const toggleAll = () => {
    const available = filteredDevices.filter(d => d.status !== 'OFFLINE').map(d => d.id);
    const allSelected = available.every(id => selectedDeviceIds.has(id));
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

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedWorkflowId) { toast.error('请选择工作流'); return; }
    if (selectedDeviceIds.size === 0) { toast.error('请至少选择一台设备'); return; }

    setShowPreview(true);
  };

  const availableCount = allDevices.filter(d => d.status !== 'OFFLINE').length;

  return (
    <div className="space-y-6 max-w-3xl">
      {showPreview && selectedWorkflowId && (
        <DispatchPreviewDialog
          open={showPreview}
          workflowId={selectedWorkflowId}
          deviceIds={Array.from(selectedDeviceIds)}
          failureThreshold={failureThreshold}
          onClose={() => setShowPreview(false)}
          onStarted={(run) => {
            toast.success('测试已发起');
            setShowPreview(false);
            navigate(`/execution/runs/${run.id}`);
          }}
        />
      )}

      <div>
        <h1 className="text-2xl font-semibold text-gray-900">发起测试</h1>
        <p className="text-gray-500 mt-1">选择工作流蓝图和目标设备，创建一次 WorkflowRun</p>
      </div>

      <form onSubmit={handleSubmit} className="space-y-4">
        {/* Step 1: Select Workflow */}
        <Card>
          <CardHeader>
            <CardTitle className="text-base">1. 选择工作流</CardTitle>
          </CardHeader>
          <CardContent>
            {wfLoading ? (
              <Skeleton className="h-10 w-full" />
            ) : (
              <>
                <select
                  value={selectedWorkflowId ?? ''}
                  onChange={e => setSelectedWorkflowId(e.target.value ? Number(e.target.value) : null)}
                  className="w-full px-3 py-2 border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-gray-900/10 bg-white"
                >
                  <option value="">— 请选择工作流 —</option>
                  {workflows?.map(wf => (
                    <option key={wf.id} value={wf.id}>{wf.name}</option>
                  ))}
                </select>
                {selectedWf?.description && (
                  <p className="mt-2 text-sm text-gray-500">{selectedWf.description}</p>
                )}
              </>
            )}
          </CardContent>
        </Card>

        {/* Step 2: Select Devices */}
        <Card>
          <CardHeader>
            <CardTitle className="text-base">
              <div className="flex items-center justify-between">
                <span>2. 选择设备</span>
                <span className="text-sm font-normal text-gray-500">
                  已选 {selectedDeviceIds.size} / {availableCount} 台可用
                </span>
              </div>
            </CardTitle>
          </CardHeader>
          <CardContent>
            {devLoading ? (
              <Skeleton className="h-40 w-full" />
            ) : allDevices.length === 0 ? (
              <div className="text-center py-6 text-gray-400">
                <Smartphone className="w-8 h-8 mx-auto mb-2 text-gray-300" />
                <p className="text-sm">暂无设备</p>
              </div>
            ) : (
              <>
                <div className="flex gap-2 mb-3">
                  <input
                    type="text"
                    placeholder="搜索设备 serial / model..."
                    value={deviceFilter}
                    onChange={e => setDeviceFilter(e.target.value)}
                    className="flex-1 px-3 py-1.5 text-sm border rounded-lg focus:outline-none focus:ring-2 focus:ring-gray-900/10"
                  />
                  <Button type="button" variant="outline" size="sm" onClick={toggleAll}>
                    全选/取消
                  </Button>
                </div>
                <div className="max-h-60 overflow-y-auto border rounded-lg divide-y">
                  {filteredDevices.map(d => (
                    <DeviceRow
                      key={d.id}
                      device={d}
                      selected={selectedDeviceIds.has(d.id)}
                      onToggle={() => toggleDevice(d.id)}
                    />
                  ))}
                </div>
              </>
            )}
          </CardContent>
        </Card>

        {/* Step 3: Failure Threshold */}
        <Card>
          <CardHeader>
            <CardTitle className="text-base">3. 失败阈值</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="flex items-center gap-3">
              <input
                type="number"
                min={0}
                max={1}
                step={0.01}
                value={failureThreshold}
                onChange={e => setFailureThreshold(parseFloat(e.target.value) || 0)}
                className="w-28 px-3 py-2 border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-gray-900/10"
              />
              <span className="text-sm text-gray-600">
                = {Math.round(failureThreshold * 100)}% 失败率（超过此比例将标记 WorkflowRun 为 FAILED）
              </span>
            </div>
          </CardContent>
        </Card>

        {/* Submit */}
        {selectedDeviceIds.size === 0 && (
          <div className="flex items-center gap-2 text-sm text-amber-600 bg-amber-50 px-3 py-2 rounded-lg">
            <AlertCircle className="w-4 h-4" />
            请至少选择一台设备
          </div>
        )}

        <div className="flex justify-end gap-2">
          <Button type="button" variant="outline" onClick={() => navigate(-1)}>
            取消
          </Button>
          <Button
            type="submit"
            disabled={!selectedWorkflowId || selectedDeviceIds.size === 0}
          >
            <Play className="w-4 h-4 mr-2" />
            预览并发起
          </Button>
        </div>
      </form>
    </div>
  );
}
