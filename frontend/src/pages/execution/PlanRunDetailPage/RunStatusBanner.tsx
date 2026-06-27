import React from 'react';
import { cn } from '@/lib/utils';
import { TEXT, ALERT_BANNER } from '@/design-system/tokens';
import type { PlanRun } from '@/utils/api/types';

interface RunStatusBannerProps {
  run: PlanRun | undefined;
}

export const RunStatusBanner: React.FC<RunStatusBannerProps> = ({ run }) => {
  if (!run) return null;
  const isFailed = run.status === 'FAILED' || run.status === 'DEGRADED';
  const isRunning = run.status === 'RUNNING';
  return (
    <div
      data-testid="plan-run-status-banner"
      className={cn(
        'px-4 py-2 text-sm border-b',
        isFailed && ALERT_BANNER.destructive,
        isRunning && ALERT_BANNER.warning,
        !isFailed && !isRunning && 'bg-muted/30 border-border',
      )}
    >
      <span className={cn('font-medium', TEXT.heading)}>{run.status}</span>
      <span className={cn('ml-2', TEXT.subtitle)}>
        {isRunning ? 'PlanRun 正在执行中' : isFailed ? 'PlanRun 执行异常' : 'PlanRun 已完成'}
      </span>
    </div>
  );
};

export default RunStatusBanner;
