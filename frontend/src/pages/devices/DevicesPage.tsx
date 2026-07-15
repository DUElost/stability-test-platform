import { useCallback, useEffect, useMemo, useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Plus, Smartphone } from 'lucide-react';
import { useToast } from '@/hooks/useToast';
import { useAuthSession } from '@/hooks/useAuthSession';
import { ExpandableDeviceTable, type DeviceTableData, type DeviceStatus } from '@/components/device/ExpandableDeviceTable';
import DeviceBulkActionBar from '@/components/device/DeviceBulkActionBar';
import { AddDeviceModal } from './components/AddDeviceModal';
import { BatchEditDeviceTagsDialog, type DeviceTagOperation } from './components/BatchEditDeviceTagsDialog';
import { DeviceMetricsModal } from './components/DeviceMetricsModal';
import { api, fetchHostList } from '@/utils/api';
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
  const [selectedDeviceIds, setSelectedDeviceIds] = useState<Set<number>>(new Set());
  const [filteredDevices, setFilteredDevices] = useState<DeviceTableData[]>([]);
  const [isTagDialogOpen, setIsTagDialogOpen] = useState(false);
  const queryClient = useQueryClient();
  const toast = useToast();
  const sessionQ = useAuthSession();
  const isAdmin = sessionQ.data?.role === 'admin';

  const { data: devices, isLoading, error } = useQuery({
    queryKey: deviceKeys.list(),
    queryFn: () => api.devices.list(0, 1200).then(res => res.items),
    refetchInterval: 10000,
  });

  const { data: hosts } = useQuery({
    queryKey: hostKeys.list(),
    queryFn: () => fetchHostList(0, 200),
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
        battery_level: device.battery_level ?? undefined,
        temperature: device.temperature ?? device.battery_temp ?? undefined,
        network_latency: device.network_latency ?? null,
        build_display_id: device.build_display_id ?? null,
        host_id: device.host_id,
        host_name: host?.name || host?.ip || null,
        current_task: device.current_task?.name,
        last_seen: device.last_seen,
        tags: Array.isArray(device.tags) ? device.tags : [],
      };
    });
  }, [devices, hostMap]);

  const selectedDevices = useMemo(
    () => formattedDevices.filter((device) => selectedDeviceIds.has(device.id)),
    [formattedDevices, selectedDeviceIds],
  );
  const filteredDeviceIds = useMemo(() => new Set(filteredDevices.map((device) => device.id)), [filteredDevices]);
  const selectedFilteredCount = useMemo(
    () => Array.from(selectedDeviceIds).filter((id) => filteredDeviceIds.has(id)).length,
    [filteredDeviceIds, selectedDeviceIds],
  );
  const statusSummary = useMemo(() => {
    const counts = {
      idle: selectedDevices.filter((device) => device.status === 'idle').length,
      testing: selectedDevices.filter((device) => device.status === 'testing').length,
      offline: selectedDevices.filter((device) => device.status === 'offline').length,
      error: selectedDevices.filter((device) => device.status === 'error').length,
    };
    return [
      counts.idle ? `空闲 ${counts.idle}` : null,
      counts.testing ? `测试中 ${counts.testing}` : null,
      counts.offline ? `离线 ${counts.offline}` : null,
      counts.error ? `错误 ${counts.error}` : null,
    ].filter(Boolean).join(' · ');
  }, [selectedDevices]);

  useEffect(() => {
    const availableIds = new Set(formattedDevices.map((device) => device.id));
    setSelectedDeviceIds((previous) => {
      const next = new Set(Array.from(previous).filter((id) => availableIds.has(id)));
      return next.size === previous.size ? previous : next;
    });
  }, [formattedDevices]);

  const handleFilteredDevicesChange = useCallback((nextDevices: DeviceTableData[]) => {
    setFilteredDevices(nextDevices);
  }, []);

  const tagUpdateMutation = useMutation({
    mutationFn: async ({
      targets,
      operation,
      tags,
    }: {
      targets: DeviceTableData[];
      operation: DeviceTagOperation;
      tags: string[];
    }) => {
      let cursor = 0;
      let succeeded = 0;
      const failed: string[] = [];
      const workers = Array.from({ length: Math.min(5, targets.length) }, async () => {
        while (cursor < targets.length) {
          const target = targets[cursor++];
          const currentTags = target.tags ?? [];
          const nextTags = operation === 'add'
            ? Array.from(new Set([...currentTags, ...tags]))
            : operation === 'remove'
              ? currentTags.filter((tag) => !tags.includes(tag))
              : tags;
          try {
            await api.devices.updateTags(target.id, nextTags);
            succeeded += 1;
          } catch {
            failed.push(target.serial);
          }
        }
      });
      await Promise.all(workers);
      return { succeeded, failed };
    },
    onSuccess: ({ succeeded, failed }) => {
      queryClient.invalidateQueries({ queryKey: deviceKeys.list() });
      setIsTagDialogOpen(false);
      if (failed.length === 0) {
        toast.success(`已更新 ${succeeded} 台设备的标签`);
        setSelectedDeviceIds(new Set());
      } else {
        toast.error(`标签更新完成：成功 ${succeeded} 台，失败 ${failed.length} 台`);
      }
    },
    onError: (error: any) => {
      toast.error(`批量更新标签失败: ${error?.message || '未知错误'}`);
    },
  });

  const handleSelectAllFiltered = () => {
    setSelectedDeviceIds((previous) => {
      const next = new Set(previous);
      filteredDevices.forEach((device) => next.add(device.id));
      return next;
    });
  };

  const handleCopySerials = async () => {
    const text = selectedDevices.map((device) => device.serial).join('\n');
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
      } else {
        const textarea = document.createElement('textarea');
        textarea.value = text;
        textarea.style.position = 'fixed';
        textarea.style.opacity = '0';
        document.body.appendChild(textarea);
        textarea.select();
        document.execCommand('copy');
        textarea.remove();
      }
      toast.success(`已复制 ${selectedDevices.length} 个设备序列号`);
    } catch {
      toast.error('复制失败，请检查浏览器剪贴板权限');
    }
  };

  const handleExportSelected = () => {
    const csvCell = (value: unknown) => `"${String(value ?? '').replace(/"/g, '""')}"`;
    const rows = selectedDevices.map((device) => [
      device.id,
      device.serial,
      device.model,
      device.status,
      device.host_name ?? '',
      device.build_display_id ?? '',
      (device.tags ?? []).join('|'),
      device.last_seen ?? '',
    ]);
    const csv = [
      ['ID', 'Serial', 'Model', 'Status', 'Host', 'Build', 'Tags', 'Last Seen'],
      ...rows,
    ].map((row) => row.map(csvCell).join(',')).join('\r\n');
    const blob = new Blob([`\uFEFF${csv}`], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `devices-${new Date().toISOString().slice(0, 10)}.csv`;
    anchor.click();
    URL.revokeObjectURL(url);
    toast.success(`已导出 ${selectedDevices.length} 台设备`);
  };

  const handleViewSelectedMetrics = () => {
    if (selectedDevices.length !== 1) return;
    const [device] = selectedDevices;
    setMetricsDevice({ id: device.id, serial: device.serial });
  };

  if (isLoading) {
    return (
      <PageContainer width="full">
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
      <PageContainer width="full">
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
      <PageContainer width="full">
        <PageHeader title="设备管理" subtitle="管理和监控测试设备" />
        <EmptyState
          title="还没有设备"
          description="添加您的第一台测试设备"
          icon={<Smartphone className="w-16 h-16" />}
          action={isAdmin ? (
            <Button onClick={() => setIsModalOpen(true)}>
              <Plus className="w-4 h-4 mr-2" />
              添加设备
            </Button>
          ) : undefined}
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
    <PageContainer
      width="full"
      className={selectedDeviceIds.size > 0 ? 'pb-28' : undefined}
    >
      <PageHeader title="设备管理" subtitle="管理和监控测试设备" />

      <div className="flex items-center justify-between gap-2 py-2">
        <span className={cn('text-xs', TEXT.subtitle)}>点击设备行展开详情，勾选后可批量处理</span>
        {isAdmin && (
          <Button onClick={() => setIsModalOpen(true)}>
            <Plus className="w-4 h-4" />
            添加设备
          </Button>
        )}
      </div>

      {/* Device Table */}
      <div>
        <ExpandableDeviceTable
          devices={formattedDevices}
          onViewMetrics={(device) => setMetricsDevice({ id: device.id, serial: device.serial })}
          selectedIds={selectedDeviceIds}
          onSelectionChange={setSelectedDeviceIds}
          onFilteredDevicesChange={handleFilteredDevicesChange}
        />
      </div>

      <DeviceBulkActionBar
        selectedCount={selectedDevices.length}
        filteredCount={filteredDevices.length}
        selectedFilteredCount={selectedFilteredCount}
        statusSummary={statusSummary}
        canEditTags={isAdmin}
        tagUpdatePending={tagUpdateMutation.isPending}
        onSelectAllFiltered={handleSelectAllFiltered}
        onEditTags={() => setIsTagDialogOpen(true)}
        onCopySerials={handleCopySerials}
        onExport={handleExportSelected}
        onViewMetrics={handleViewSelectedMetrics}
        onClear={() => setSelectedDeviceIds(new Set())}
      />

      <AddDeviceModal
        isOpen={isModalOpen}
        onClose={() => setIsModalOpen(false)}
        onSubmit={(data) => createMutation.mutate(data)}
        isSubmitting={createMutation.isPending}
      />

      <BatchEditDeviceTagsDialog
        isOpen={isTagDialogOpen}
        selectedCount={selectedDevices.length}
        isSubmitting={tagUpdateMutation.isPending}
        onClose={() => setIsTagDialogOpen(false)}
        onSubmit={(operation, tags) => {
          tagUpdateMutation.mutate({ targets: selectedDevices, operation, tags });
        }}
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
