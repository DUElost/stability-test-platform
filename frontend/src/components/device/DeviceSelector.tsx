import React from 'react';
import { Device } from './DeviceCard';

interface DeviceSelectorProps {
  devices: Device[];
  selectedSerials: string[];
  onChange: (serials: string[]) => void;
}

export const DeviceSelector: React.FC<DeviceSelectorProps> = ({ devices, selectedSerials, onChange }) => {
  const toggleDevice = (serial: string) => {
    if (selectedSerials.includes(serial)) {
      onChange(selectedSerials.filter(s => s !== serial));
    } else {
      onChange([...selectedSerials, serial]);
    }
  };

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3 max-h-60 overflow-y-auto p-1">
      {devices.map(device => (
        <label key={device.serial} className={`flex items-center p-3 border rounded cursor-pointer transition-colors focus-within:ring-2 focus-within:ring-indigo-500 focus-within:ring-offset-1
          ${selectedSerials.includes(device.serial) ? 'border-indigo-500 bg-indigo-50' : 'border-slate-200 hover:bg-slate-50'}`}>
          <input type="checkbox" className="mr-3 h-4 w-4 text-indigo-600 rounded"
            checked={selectedSerials.includes(device.serial)}
            onChange={() => toggleDevice(device.serial)}
          />
          <div className="text-sm">
            <div className="font-medium text-slate-800">{device.model}</div>
            <div className="text-xs text-slate-500 font-mono">{device.serial}</div>
          </div>
        </label>
      ))}
    </div>
  );
};
