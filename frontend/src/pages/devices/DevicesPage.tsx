import { useState, useMemo } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Plus, Smartphone } from 'lucide-react';
import { useToast } from '@/hooks/useToast';
import { ExpandableDeviceTable, type DeviceTableData, type DeviceStatus } from '@/components/device/ExpandableDeviceTable';
import { AddDeviceModal } from './components/AddDeviceModal';
import { DeviceMetricsModal } from './components/DeviceMetricsModal';
import { api } from '@/utils/api';
import { deviceKeys, hostKeys } from '@/utils/api/queryKeys';
import { Button } from '@/components/ui/button';
import { PageContainer, PageHeader } from '@/components/layout';
import { ErrorState } from '@/components/ui/error-state';
import { EmptyState } from '@/components/ui/empty-state';
import { SKELETON_BLOCK, TEXT } from '@/design-system';
import { cn } from '@/lib/utils';

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
    queryKey: deviceKeys.list(),
    queryFn: () => api.devices.list(0, 1200).then(res => res.data.items),
    refetchInterval: 10000,
  });

  const { data: hosts } = useQuery({
    queryKey: hostKeys.list(),
    queryFn: () => api.hosts.list(0, 200).then(res => res.data.items),
    refetchInterval: 10000,
  });

  const createMutation = useMutation({
    mutationFn: (data: { serial: string; model?: string; host_id?: number; tags?: string[] }) =>
      api.devices.create(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: deviceKeys.list() });
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
        model: device.model || '未知设备',
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
      <PageContainer width="wide">
        <PageHeader title="设备管理" subtitle="管理和监控测试设备" />
        <div className="space-y-4">
          <div className={cn('h-32', SKELETON_BLOCK)} />
          <div className={cn('h-64', SKELETON_BLOCK)} />
        </div>
      </PageContainer>
    );
  }

  if (error) {
    return (
      <PageContainer width="wide">
        <PageHeader title="设备管理" subtitle="管理和监控测试设备" />
        <ErrorState
          title="加载设备失败"
          description="请检查后端服务连接"
          onRetry={() => queryClient.invalidateQueries({ queryKey: deviceKeys.list() })}
        />
      </PageContainer>
    );
  }

  if (formattedDevices.length === 0) {
    return (
      <PageContainer width="wide">
        <PageHeader title="设备管理" subtitle="管理和监控测试设备" />
        <EmptyState
          title="还没有设备"
          description="添加您的第一台测试设备"
          icon={<Smartphone className="w-16 h-16" />}
          action={
            <Button onClick={() => setIsModalOpen(true)}>
              <Plus className="w-4 h-4 mr-2" />
              添加设备
            </Button>
          }
        />
        <AddDeviceModal
          isOpen={isModalOpen}
          onClose={() => setIsModalOpen(false)}
          onSubmit={createMutation.mutate}
          isSubmitting={createMutation.isPending}
        />
      </PageContainer>
    );
  }

  return (
    <PageContainer width="wide">
      <PageHeader
        title="设备管理"
        subtitle="管理和监控测试设备"
        action={
          <Button onClick={() => setIsModalOpen(true)}>
            <Plus className="w-4 h-4" />
            添加设备
          </Button>
        }
      />

      {/* Device Table */}
      <div>
        <div className="flex justify-end mb-2">
          <span className={cn('text-xs', TEXT.subtitle)}>点击设备行的指标按钮查看历史数据</span>
        </div>
        <ExpandableDeviceTable
          devices={formattedDevices}
          onViewMetrics={(device) => setMetricsDevice({ id: device.id, serial: device.serial })}
        />
      </div>

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
    </PageContainer>
  );
}
