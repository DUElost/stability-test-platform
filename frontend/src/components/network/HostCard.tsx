import React from 'react';
import { ConnectivityBadge } from './ConnectivityBadge';
import { Smartphone } from 'lucide-react';

export interface Host {
  ip: string;
  status: 'online' | 'offline' | 'warning';
  cpu_load: number;
  ram_usage: number;
  disk_usage: number;
  mount_status: boolean;
  device_count?: number; // 新增：连接的设备数量
}

interface ProgressBarProps {
  label: string;
  value: number;
  colorClass?: string;
}

const ProgressBar: React.FC<ProgressBarProps> = ({ label, value, colorClass = "bg-blue-600" }) => (
  <div className="mb-2">
    <div className="flex justify-between text-xs mb-1">
      <span className="text-slate-500">{label}</span>
      <span className="font-medium text-slate-700">{value}%</span>
    </div>
    <div
      className="w-full bg-slate-200 rounded-full h-1.5"
      role="progressbar"
      aria-valuenow={value}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-label={label}
    >
      <div className={`${colorClass} h-1.5 rounded-full transition-all`} style={{ width: `${value}%` }}></div>
    </div>
  </div>
);

export const HostCard: React.FC<{ host: Host }> = ({ host }) => {
  return (
    <div className="bg-white rounded-lg shadow-sm border border-slate-200 p-4 w-full card-hover">
      <div className="flex justify-between items-start mb-4">
        <div className="min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <h3 className="font-semibold text-slate-900">{host.ip}</h3>
            {/* 设备数量 Badge */}
            {typeof host.device_count === 'number' && (
              <span
                className="inline-flex items-center gap-1 text-[10px] px-2 py-0.5 bg-blue-50 text-blue-700 rounded-full font-medium"
                title={`${host.device_count} device${host.device_count !== 1 ? 's' : ''} connected`}
              >
                <Smartphone size={10} />
                {host.device_count}
              </span>
            )}
          </div>
          <p className="text-xs text-slate-500 mt-0.5">Host Node</p>
        </div>
        <ConnectivityBadge status={host.status} />
      </div>

      <div className="space-y-3">
        <ProgressBar label="CPU Load" value={host.cpu_load} colorClass={host.cpu_load > 80 ? 'bg-red-500' : 'bg-blue-500'} />
        <ProgressBar label="RAM Usage" value={host.ram_usage} colorClass={host.ram_usage > 80 ? 'bg-yellow-500' : 'bg-purple-500'} />
        <ProgressBar label="Disk Usage" value={host.disk_usage} />
      </div>

      <div className="mt-4 pt-3 border-t border-slate-100 flex items-center justify-between text-sm">
        <span className="text-slate-600">Storage Mount</span>
        <span className={`px-2 py-0.5 rounded text-xs font-medium ${host.mount_status ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'}`}>
          {host.mount_status ? 'MOUNTED' : 'UNMOUNTED'}
        </span>
      </div>
    </div>
  );
};
