import { ParameterProjectionList } from '@/components/parameters/ParameterProjectionList';
import type { ParameterProjection } from '@/utils/api/types';

interface PlanStepListProps {
  projection: ParameterProjection | null | undefined;
  unavailable?: boolean;
}

export function PlanStepList({ projection, unavailable = false }: PlanStepListProps) {
  return (
    <div className="max-h-72 overflow-y-auto" data-testid="plan-step-list">
      <ParameterProjectionList projection={projection} unavailable={unavailable} />
    </div>
  );
}
