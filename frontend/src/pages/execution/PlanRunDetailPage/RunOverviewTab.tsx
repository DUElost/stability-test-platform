import React from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { TEXT, STAT } from '@/design-system/tokens';
import { cn } from '@/lib/utils';
import type { PlanRun } from '@/utils/api/types';

// Overview may receive extended count fields from the backend; fall back to 0
// if they are not present and derive what we can from result_summary.
type OverviewRun = PlanRun & {
  device_count?: number | null;
  completed_devices?: number | null;
  failed_devices?: number | null;
  artifact_count?: number | null;
};

interface RunOverviewTabProps {
  run: PlanRun | undefined;
}

export const RunOverviewTab: React.FC<RunOverviewTabProps> = ({ run }) => {
  if (!run) return null;
  const overviewRun = run as OverviewRun;
  const deviceCount = overviewRun.device_count ?? overviewRun.result_summary?.total ?? 0;
  const completedCount = overviewRun.completed_devices ?? overviewRun.result_summary?.completed ?? 0;
  const failedCount = overviewRun.failed_devices ?? overviewRun.result_summary?.failed ?? 0;
  const artifactCount = overviewRun.artifact_count ?? 0;

  return (
    <div className="p-4 space-y-4">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <Card>
          <CardContent className="py-4 text-center">
            <p className={STAT.value}>{deviceCount}</p>
            <p className={STAT.label}>设备数</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="py-4 text-center">
            <p className={STAT.value}>{completedCount}</p>
            <p className={STAT.label}>已完成</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="py-4 text-center">
            <p className={STAT.value}>{failedCount}</p>
            <p className={STAT.label}>失败</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="py-4 text-center">
            <p className={STAT.value}>{artifactCount}</p>
            <p className={STAT.label}>产物数</p>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className={cn('text-sm', TEXT.heading)}>PlanRun 信息</CardTitle>
        </CardHeader>
        <CardContent className={cn('text-sm space-y-2', TEXT.subtitle)}>
          <p>Plan ID: {run.plan_id}</p>
          <p>Run Type: {run.run_type}</p>
          <p>Trigger: {run.triggered_by || '-'}</p>
        </CardContent>
      </Card>
    </div>
  );
};

export default RunOverviewTab;
