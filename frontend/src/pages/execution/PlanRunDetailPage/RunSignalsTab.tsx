import React from 'react';
import { DataErrorState } from '@/components/data';
import AnomalyDashboard from '@/components/plan-run/AnomalyDashboard';
import type { WatcherSummary } from '@/utils/api/types';

interface RunSignalsTabProps {
  runId: number;
  summary: WatcherSummary | undefined;
  isLoading: boolean;
  isError: boolean;
  onRefresh: () => void;
}

export const RunSignalsTab: React.FC<RunSignalsTabProps> = ({
  runId,
  summary,
  isLoading,
  isError,
  onRefresh,
}) => {
  return (
    <div className="p-4 space-y-4">
      <AnomalyDashboard
        runId={runId}
        data={summary}
        isLoading={isLoading}
        isError={isError}
        timeScope="all"
        onTimeScopeChange={() => {}}
      />
      {isError && <DataErrorState onRetry={onRefresh} />}
    </div>
  );
};

export default RunSignalsTab;
