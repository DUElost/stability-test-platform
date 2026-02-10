import React from 'react';
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
      {devices.map(device => {
        const deviceId = device.id;
        if (typeof deviceId !== 'number') {
          return null;
        }
        return (
        <label key={device.serial} className={`flex items-center p-3 border rounded cursor-pointer transition-colors focus-within:ring-2 focus-within:ring-indigo-500 focus-within:ring-offset-1
          ${selectedDeviceIds.includes(deviceId) ? 'border-indigo-500 bg-indigo-50' : 'border-slate-200 hover:bg-slate-50'}`}>
          <input type="checkbox" className="mr-3 h-4 w-4 text-indigo-600 rounded"
            checked={selectedDeviceIds.includes(deviceId)}
            onChange={() => toggleDevice(deviceId)}
          />
          <div className="text-sm">
            <div className="font-medium text-slate-800">{device.model}</div>
            <div className="text-xs text-slate-500 font-mono">{device.serial}</div>
          </div>
        </label>
      )})}
    </div>
  );
};
