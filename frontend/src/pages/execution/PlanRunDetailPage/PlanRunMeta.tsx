import React from 'react';
import { cn } from '@/lib/utils';
import { TEXT } from '@/design-system/tokens';
import { formatLocalDateTime } from '@/utils/format';
import type { PlanRun } from '@/utils/api/types';

interface PlanRunMetaProps {
  run: PlanRun | undefined;
}

export const PlanRunMeta: React.FC<PlanRunMetaProps> = ({ run }) => {
  if (!run) return null;
  return (
    <div className={cn('flex flex-wrap items-center gap-x-4 gap-y-1 text-xs', TEXT.subtitle)}>
      <span>状态: {run.status}</span>
      {run.started_at && <span>开始: {formatLocalDateTime(run.started_at)}</span>}
      {run.ended_at && <span>结束: {formatLocalDateTime(run.ended_at)}</span>}
      {run.triggered_by && <span>触发: {run.triggered_by}</span>}
    </div>
  );
};

export default PlanRunMeta;
