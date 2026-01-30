import React from 'react';
import { ConnectivityBadge } from '../network/ConnectivityBadge';
import { AlertTriangle, Activity, Server } from 'lucide-react';

export interface Device {
  serial: string;
  model: string;
  status: 'idle' | 'testing' | 'offline' | 'error';
  battery_level: number;
  temperature: number;
  network_latency?: number | null;  // ms (ping 8.8.8.8 / 223.5.5.5)
  current_task?: string;
  host_name?: string;      // 新增：所属主机名称/IP
  host_id?: number | null; // 新增：所属主机ID
}

export const DeviceCard: React.FC<{ device: Device; onClick?: (d: Device) => void }> = ({ device, onClick }) => {
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
    <div
      onClick={() => onClick?.(device)}
      className={`bg-white rounded-lg shadow-sm p-4 ${statusColors[device.status]} card-hover cursor-pointer relative group`}
    >
      <div className="flex justify-between items-start mb-2">
        <div className="min-w-0 flex-1">
          <h4 className="font-bold text-slate-800 text-sm truncate">{device.model}</h4>
          <p className="text-xs font-mono text-slate-500">{device.serial}</p>
          {/* Host 标签 */}
          {device.host_id && (
            <div className="mt-1.5 flex items-center gap-1">
              <Server size={10} className="text-slate-400 flex-shrink-0" />
              <span
                className={`text-[10px] px-1.5 py-0.5 rounded font-medium truncate max-w-[150px] ${
                  device.host_name
                    ? 'bg-slate-100 text-slate-600'
                    : 'bg-amber-50 text-amber-600'
                }`}
                title={device.host_name || `Host ID: ${device.host_id}`}
              >
                {device.host_name || `Host #${device.host_id}`}
              </span>
            </div>
          )}
        </div>
        <span className={`text-[10px] uppercase font-bold px-2 py-0.5 rounded-full flex-shrink-0
          ${device.status === 'testing' ? 'bg-blue-100 text-blue-700' :
            device.status === 'error' ? 'bg-red-100 text-red-700' : 'bg-slate-100 text-slate-600'}`}>
          {device.status}
        </span>
      </div>

      {device.status === 'testing' && device.current_task && (
        <div className="mb-3 bg-blue-50 px-2 py-1.5 rounded border border-blue-100 flex items-center gap-2">
          <Activity size={12} className="text-blue-500 animate-pulse" />
          <span className="text-xs font-medium text-blue-700 truncate">{device.current_task}</span>
        </div>
      )}

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
            <div className={`bg-slate-50 p-1.5 rounded ${device.temperature > 45 ? 'bg-red-50 border border-red-100' : ''}`}>
              <span className="text-slate-400 block mb-1">Temp</span>
              <div className="flex items-center justify-between">
                <span className={`font-mono font-bold ${device.temperature > 40 ? 'text-red-600' : 'text-slate-700'}`}>{device.temperature}°C</span>
                {device.temperature > 45 && <AlertTriangle size={12} className="text-red-500" />}
              </div>
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
