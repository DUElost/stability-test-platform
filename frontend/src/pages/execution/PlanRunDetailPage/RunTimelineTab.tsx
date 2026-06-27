import React from 'react';
import BusinessFlowStepper from '@/components/plan-run/BusinessFlowStepper';
import type { PlanRunTimeline } from '@/utils/api/types';

interface RunTimelineTabProps {
  timeline: PlanRunTimeline | undefined;
  isLoading: boolean;
  isError: boolean;
}

export const RunTimelineTab: React.FC<RunTimelineTabProps> = ({ timeline, isLoading, isError }) => {
  return (
    <div className="p-4">
      <BusinessFlowStepper
        timeline={timeline}
        isLoading={isLoading}
        isError={isError}
      />
    </div>
  );
};

export default RunTimelineTab;
