import React from 'react';
import { BORDER, INTERACTIVE, PIPELINE_EDITOR, TEXT } from '@/design-system/tokens';
import { cn } from '@/lib/utils';
import { Device } from './DeviceCard';

interface DeviceSelectorProps {
  devices: Device[];
  selectedDeviceIds: number[];
  onChange: (deviceIds: number[]) => void;
}

export const DeviceSelector: React.FC<DeviceSelectorProps> = ({ devices, selectedDeviceIds, onChange }) => {
  const toggleDevice = (deviceId: number) => {
    if (selectedDeviceIds.includes(deviceId)) {
      onChange(selectedDeviceIds.filter((id) => id !== deviceId));
    } else {
      onChange([...selectedDeviceIds, deviceId]);
    }
  };

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3 max-h-60 overflow-y-auto p-1">
      {devices.map((device) => {
        const deviceId = device.id;
        if (typeof deviceId !== 'number') {
          return null;
        }
        const selected = selectedDeviceIds.includes(deviceId);
        return (
          <label
            key={device.serial}
            className={cn(
              'flex items-center p-3 border rounded cursor-pointer transition-colors',
              'focus-within:ring-2 focus-within:ring-ring focus-within:ring-offset-1',
              selected
                ? PIPELINE_EDITOR.stepSelected
                : cn(BORDER.default, INTERACTIVE.hover, 'hover:bg-accent/50'),
            )}
          >
            <input
              type="checkbox"
              className="mr-3 h-4 w-4 text-primary rounded border-border"
              checked={selected}
              onChange={() => toggleDevice(deviceId)}
            />
            <div className="text-sm">
              <div className={cn('font-medium', TEXT.heading)}>{device.model}</div>
              <div className={cn('text-xs font-mono', TEXT.subtitle)}>{device.serial}</div>
            </div>
          </label>
        );
      })}
    </div>
  );
};
