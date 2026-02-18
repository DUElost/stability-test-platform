import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { X, Loader2 } from 'lucide-react';
import { api } from '@/utils/api';
import { DeviceMetricsChart } from '@/components/charts/DeviceMetricsChart';

interface DeviceMetricsModalProps {
  isOpen: boolean;
  onClose: () => void;
  deviceId: number;
  deviceSerial: string;
}

export function DeviceMetricsModal({ isOpen, onClose, deviceId, deviceSerial }: DeviceMetricsModalProps) {
  const [hours, setHours] = useState(24);

  const { data, isLoading } = useQuery({
    queryKey: ['device-metrics', deviceId, hours],
    queryFn: () => api.stats.deviceMetrics(deviceId, hours).then((res) => res.data),
    enabled: isOpen,
    refetchInterval: 30000,
  });

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="fixed inset-0 bg-black/40" onClick={onClose} />
      <div className="relative bg-white rounded-xl shadow-xl w-full max-w-2xl mx-4 max-h-[85vh] overflow-auto">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b">
          <div>
            <h3 className="text-lg font-semibold text-gray-900">设备指标历史</h3>
            <p className="text-sm text-gray-500">{deviceSerial}</p>
          </div>
          <div className="flex items-center gap-3">
            <select
              value={hours}
              onChange={(e) => setHours(Number(e.target.value))}
              className="text-sm border rounded-md px-2 py-1"
            >
              <option value={6}>最近6小时</option>
              <option value={24}>最近24小时</option>
              <option value={72}>最近3天</option>
              <option value={168}>最近7天</option>
            </select>
            <button onClick={onClose} className="text-gray-400 hover:text-gray-600">
              <X size={20} />
            </button>
          </div>
        </div>

        {/* Body */}
        <div className="p-6">
          {isLoading ? (
            <div className="flex items-center justify-center h-64">
              <Loader2 className="w-8 h-8 animate-spin text-gray-400" />
            </div>
          ) : (
            <DeviceMetricsChart data={data?.points || []} />
          )}
        </div>
      </div>
    </div>
  );
}
