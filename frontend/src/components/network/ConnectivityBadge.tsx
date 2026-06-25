import React from 'react';
import { STATUS_CHIP } from '@/design-system/tokens';
import { cn } from '@/lib/utils';

interface ConnectivityBadgeProps {
  status: 'online' | 'offline' | 'warning';
  latency?: number;
}

const STATUS_STYLE = {
  online: { chip: STATUS_CHIP.success, dot: 'bg-success' },
  offline: { chip: STATUS_CHIP.destructive, dot: 'bg-destructive' },
  warning: { chip: STATUS_CHIP.warning, dot: 'bg-warning' },
} as const;

export const ConnectivityBadge: React.FC<ConnectivityBadgeProps> = ({ status, latency }) => {
  const style = STATUS_STYLE[status];

  return (
    <div className={cn('inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium', style.chip)}>
      <span className={cn('mr-1.5 h-2 w-2 rounded-full', style.dot)} />
      <span className="capitalize">{status}</span>
      {latency !== undefined && (
        <span className="ml-1 opacity-75">({latency}ms)</span>
      )}
    </div>
  );
};
