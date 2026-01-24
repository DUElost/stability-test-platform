import React from 'react';
import { DeviceCard, Device } from './DeviceCard';

interface DeviceGridProps {
  devices: Device[];
  isLoading?: boolean;
}

export const DeviceGrid: React.FC<DeviceGridProps> = ({ devices, isLoading }) => {
  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-64 text-slate-500">
        Loading devices...
      </div>
    );
  }

  if (!devices || devices.length === 0) {
    return (
      <div className="flex items-center justify-center h-64 text-slate-400 bg-slate-50 rounded-lg border border-dashed border-slate-300">
        No devices matching your criteria
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
