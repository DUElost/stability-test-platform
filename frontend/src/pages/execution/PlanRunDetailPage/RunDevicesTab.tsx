import React from 'react';
import { DataTable } from '@/components/data';
import type { DeviceMatrixItem } from '@/utils/api/types';
import type { ColumnDef } from '@tanstack/react-table';

interface RunDevicesTabProps {
  runId: number;
  devices: DeviceMatrixItem[];
  isLoading: boolean;
  error: unknown;
}

export const RunDevicesTab: React.FC<RunDevicesTabProps> = ({ devices, isLoading, error }) => {
  const columns: ColumnDef<DeviceMatrixItem>[] = [
    { accessorKey: 'device_serial', header: '序列号' },
    { accessorKey: 'ui_status', header: '状态' },
    { accessorKey: 'host_id', header: '主机' },
  ];

  return (
    <div className="p-4">
      <DataTable
        data={devices}
        columns={columns}
        isLoading={isLoading}
        error={error instanceof Error ? error : null}
        emptyState={<div className="py-8 text-center text-sm text-muted-foreground">暂无设备</div>}
      />
    </div>
  );
};

export default RunDevicesTab;
