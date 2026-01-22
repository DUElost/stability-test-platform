import React from 'react';
import { ConnectivityBadge } from '../network/ConnectivityBadge';

export interface Device {
  serial: string;
  model: string;
  status: 'idle' | 'testing' | 'offline' | 'error';
  battery_level: number;
  temperature: number;
  network_latency?: number | null;  // ms (ping 8.8.8.8 / 223.5.5.5)
  current_task?: string;
}

export const DeviceCard: React.FC<{ device: Device }> = ({ device }) => {
  const statusColors = {
    idle: 'border-l-4 border-green-400',
    testing: 'border-l-4 border-blue-400',
    offline: 'border-l-4 border-slate-300 opacity-60',
    error: 'border-l-4 border-red-500',
  };

  // 根据网络延迟确定连接状态
  const getNetworkStatus = (): 'online' | 'offline' | 'warning' => {
    if (device.network_latency === null || device.network_latency === undefined) {
      return 'offline';
    }
    if (device.network_latency > 200) {
      return 'warning';
    }
    return 'online';
  };

  return (
    <div className={`bg-white rounded shadow-sm p-4 ${statusColors[device.status]} hover:shadow-md transition-shadow`}>
      <div className="flex justify-between items-start mb-2">
        <div>
          <h4 className="font-bold text-slate-800 text-sm">{device.model}</h4>
          <p className="text-xs font-mono text-slate-500">{device.serial}</p>
        </div>
        <span className={`text-[10px] uppercase font-bold px-2 py-0.5 rounded-full
          ${device.status === 'testing' ? 'bg-blue-100 text-blue-700' :
            device.status === 'error' ? 'bg-red-100 text-red-700' : 'bg-slate-100 text-slate-600'}`}>
          {device.status}
        </span>
      </div>

      {device.status !== 'offline' && (
        <div className="space-y-3 mt-3 text-xs">
          {/* 第一行：Battery & Temp */}
          <div className="grid grid-cols-2 gap-2">
            <div className="bg-slate-50 p-1.5 rounded">
              <span className="text-slate-400 block mb-1">Battery</span>
              <div className="flex items-center gap-1">
                <div
                  className="w-full bg-slate-200 h-1.5 rounded-full overflow-hidden"
                  role="progressbar"
                  aria-valuenow={device.battery_level}
                  aria-valuemin={0}
                  aria-valuemax={100}
                  aria-label="Battery Level"
                >
                  <div className={`h-full ${device.battery_level < 20 ? 'bg-red-500' : 'bg-green-500'}`} style={{ width: `${device.battery_level}%` }}></div>
                </div>
                <span className="font-mono">{device.battery_level}%</span>
              </div>
            </div>
            <div className="bg-slate-50 p-1.5 rounded">
              <span className="text-slate-400 block mb-1">Temp</span>
              <span className={`font-mono font-bold ${device.temperature > 40 ? 'text-red-600' : 'text-slate-700'}`}>{device.temperature}°C</span>
            </div>
          </div>

          {/* 第二行：Network Connectivity */}
          <div className="bg-slate-50 p-1.5 rounded">
            <div className="flex items-center justify-between">
              <span className="text-slate-400">Network</span>
              <ConnectivityBadge
                status={getNetworkStatus()}
                latency={device.network_latency ?? undefined}
              />
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
