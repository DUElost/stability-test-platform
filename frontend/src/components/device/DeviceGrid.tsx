import React from 'react';
import { BORDER, SURFACE, TEXT } from '@/design-system/tokens';
import { cn } from '@/lib/utils';
import { DeviceCard, Device } from './DeviceCard';

interface DeviceGridProps {
  devices: Device[];
  isLoading?: boolean;
}

export const DeviceGrid: React.FC<DeviceGridProps> = ({ devices, isLoading }) => {
  if (isLoading) {
    return (
      <div className={cn('flex items-center justify-center h-64', TEXT.subtitle)}>
        加载设备中...
      </div>
    );
  }

  if (!devices || devices.length === 0) {
    return (
      <div
        className={cn(
          'flex items-center justify-center h-64 rounded-lg border border-dashed',
          SURFACE.subtle,
          BORDER.default,
          TEXT.subtle,
        )}
      >
        没有符合条件的设备
      </div>
    );
  }

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
      {devices.map((device) => (
        <div key={device.serial} className="p-1">
          <DeviceCard device={device} />
        </div>
      ))}
    </div>
  );
};
