import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Loader2 } from 'lucide-react';
import { api } from '@/utils/api';
import { DeviceMetricsChart } from '@/components/charts/DeviceMetricsChart';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from '@/components/ui/dialog';
import { FORM, TEXT } from '@/design-system';
import { InlineError } from '@/components/ui/error-state';
import { cn } from '@/lib/utils';

interface DeviceMetricsModalProps {
  isOpen: boolean;
  onClose: () => void;
  deviceId: number;
  deviceSerial: string;
}

export function DeviceMetricsModal({ isOpen, onClose, deviceId, deviceSerial }: DeviceMetricsModalProps) {
  const [hours, setHours] = useState(24);

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['device-metrics', deviceId, hours],
    queryFn: () => api.stats.deviceMetrics(deviceId, hours),
    enabled: isOpen,
    refetchInterval: 30000,
  });

  return (
    <Dialog open={isOpen} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <div className="flex items-center justify-between gap-3 pr-8">
            <div>
              <DialogTitle>设备指标历史</DialogTitle>
              <DialogDescription>{deviceSerial}</DialogDescription>
            </div>
            <select
              value={hours}
              onChange={(e) => setHours(Number(e.target.value))}
              className={FORM.select}
              aria-label="时间范围"
            >
              <option value={6}>最近6小时</option>
              <option value={24}>最近24小时</option>
              <option value={72}>最近3天</option>
              <option value={168}>最近7天</option>
            </select>
          </div>
        </DialogHeader>

        <div>
          {isLoading ? (
            <div className="flex h-64 items-center justify-center">
              <Loader2 className={cn('h-8 w-8 animate-spin', TEXT.subtitle)} />
            </div>
          ) : isError ? (
            <div className="flex h-64 items-center justify-center">
              <InlineError
                message="设备指标加载失败，请稍后重试"
                onRetry={() => void refetch()}
              />
            </div>
          ) : (
            <DeviceMetricsChart data={data?.points || []} />
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
