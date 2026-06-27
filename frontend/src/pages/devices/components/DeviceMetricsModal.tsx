import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { X, Loader2 } from 'lucide-react';
import { api } from '@/utils/api';
import { DeviceMetricsChart } from '@/components/charts/DeviceMetricsChart';
import { FORM, MODAL, TEXT } from '@/design-system';
import { cn } from '@/lib/utils';

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
    queryFn: () => api.stats.deviceMetrics(deviceId, hours),
    enabled: isOpen,
    refetchInterval: 30000,
  });

  if (!isOpen) return null;

  return (
    <div className={MODAL.overlay}>
      <div className={cn(MODAL.panel, 'relative max-h-[85vh] w-full max-w-2xl overflow-auto')}>
        <div className={MODAL.header}>
          <div>
            <h3 className={MODAL.title}>设备指标历史</h3>
            <p className={cn('text-sm', TEXT.subtitle)}>{deviceSerial}</p>
          </div>
          <div className="flex items-center gap-3">
            <select
              value={hours}
              onChange={(e) => setHours(Number(e.target.value))}
              className={FORM.select}
            >
              <option value={6}>最近6小时</option>
              <option value={24}>最近24小时</option>
              <option value={72}>最近3天</option>
              <option value={168}>最近7天</option>
            </select>
            <button onClick={onClose} className={MODAL.closeButton} aria-label="关闭">
              <X size={20} />
            </button>
          </div>
        </div>

        <div className="p-6">
          {isLoading ? (
            <div className="flex h-64 items-center justify-center">
              <Loader2 className={cn('h-8 w-8 animate-spin', TEXT.subtitle)} />
            </div>
          ) : (
            <DeviceMetricsChart data={data?.points || []} />
          )}
        </div>
      </div>
    </div>
  );
}
