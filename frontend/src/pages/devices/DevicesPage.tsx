import { useState, useMemo } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Plus, Loader2 } from 'lucide-react';
import { useToast } from '../../components/ui/toast';
import { ExpandableDeviceTable, type DeviceTableData, type DeviceStatus } from '../../components/device/ExpandableDeviceTable';
import { AddDeviceModal } from './components/AddDeviceModal';
import { DeviceMetricsModal } from './components/DeviceMetricsModal';
import { api } from '../../utils/api';
import { CleanCard } from '../../components/ui/clean-card';
import { CleanButton } from '../../components/ui/clean-button';

const deviceStatusMap: Record<string, DeviceStatus> = {
  'ONLINE': 'idle',
  'BUSY': 'testing',
  'OFFLINE': 'offline',
  'ERROR': 'error'
};

export default function DevicesPage() {
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [metricsDevice, setMetricsDevice] = useState<{ id: number; serial: string } | null>(null);
  const queryClient = useQueryClient();
  const toast = useToast();

  const { data: devices, isLoading, error } = useQuery({
    queryKey: ['devices'],
    queryFn: () => api.devices.list(0, 200).then(res => res.data.items),
    refetchInterval: 10000,
  });

  const { data: hosts } = useQuery({
    queryKey: ['hosts'],
    queryFn: () => api.hosts.list(0, 200).then(res => res.data.items),
    refetchInterval: 10000,
  });

  const createMutation = useMutation({
    mutationFn: (data: { serial: string; model?: string; host_id?: number; tags?: string[] }) =>
      api.devices.create(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['devices'] });
      setIsModalOpen(false);
      toast.success('设备添加成功');
    },
    onError: (error: any) => {
      toast.error(`添加设备失败: ${error.response?.data?.detail || error.message}`);
    },
  });

  const hostMap = useMemo(() => {
    if (!hosts) return new Map<number, any>();
    return new Map(hosts.map((h: any) => [h.id, h]));
  }, [hosts]);

  const formattedDevices: DeviceTableData[] = useMemo(() => {
    if (!devices) return [];
    return devices.map((device: any) => {
      const host = device.host_id ? hostMap.get(device.host_id) : null;
      return {
        id: device.id,
        serial: device.serial,
        model: device.model || 'Unknown Device',
        status: deviceStatusMap[device.status] || 'offline',
        battery_level: device.battery_level ?? 0,
        temperature: device.temperature ?? 0,
        network_latency: device.network_latency ?? null,
        build_display_id: device.build_display_id ?? null,
        host_id: device.host_id,
        host_name: host?.name || host?.ip || null,
        current_task: device.current_task?.name,
        last_seen: device.last_seen,
      };
    });
  }, [devices, hostMap]);

  if (isLoading) {
    return (
      <div className="space-y-6">
        <div>
          <h2 className="text-2xl font-semibold text-gray-900 mb-1">设备管理</h2>
          <p className="text-sm text-gray-400">管理和监控测试设备</p>
        </div>
        <div className="flex items-center justify-center h-64">
          <Loader2 className="w-8 h-8 animate-spin text-gray-400" />
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="space-y-6">
        <div>
          <h2 className="text-2xl font-semibold text-gray-900 mb-1">设备管理</h2>
          <p className="text-sm text-gray-400">管理和监控测试设备</p>
        </div>
        <div className="p-4 bg-red-50 text-red-600 rounded-lg border border-red-100">
          加载设备失败，请检查后端服务连接。
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Page Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-semibold text-gray-900 mb-1">设备管理</h2>
          <p className="text-sm text-gray-400">管理和监控测试设备</p>
        </div>
        <CleanButton variant="primary" onClick={() => setIsModalOpen(true)}>
          <Plus className="w-4 h-4" />
          添加设备
        </CleanButton>
      </div>

      {/* Device Table */}
      {formattedDevices.length > 0 ? (
        <div>
          <div className="flex justify-end mb-2">
            <span className="text-xs text-gray-400">点击设备行的指标按钮查看历史数据</span>
          </div>
          <ExpandableDeviceTable
            devices={formattedDevices}
            onViewMetrics={(device) => setMetricsDevice({ id: device.id, serial: device.serial })}
          />
        </div>
      ) : (
        <CleanCard className="p-12 text-center">
          <h3 className="text-lg font-medium text-gray-900 mb-2">暂无设备</h3>
          <p className="text-sm text-gray-400 mb-4">添加您的第一台设备以开始使用。</p>
          <CleanButton variant="primary" onClick={() => setIsModalOpen(true)}>
            <Plus className="w-4 h-4" />
            添加设备
          </CleanButton>
        </CleanCard>
      )}

      <AddDeviceModal
        isOpen={isModalOpen}
        onClose={() => setIsModalOpen(false)}
        onSubmit={(data) => createMutation.mutate(data)}
        isSubmitting={createMutation.isPending}
      />

      {metricsDevice && (
        <DeviceMetricsModal
          isOpen={!!metricsDevice}
          onClose={() => setMetricsDevice(null)}
          deviceId={metricsDevice.id}
          deviceSerial={metricsDevice.serial}
        />
      )}
    </div>
  );
}
