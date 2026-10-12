import { ParameterProjectionList } from './ParameterProjectionList';
import type { ParameterProjection } from '@/utils/api/types';

export interface PlanSettingsReadonlyProps {
  projection?: ParameterProjection;
  unavailable?: boolean;
  isDraft?: boolean;
}

export function PlanSettingsReadonly({ projection, unavailable, isDraft }: PlanSettingsReadonlyProps) {
  return (
    <section className="rounded-md border bg-card [&_[data-testid=parameter-steps]]:hidden" aria-label="计划设置只读区">
      <div className="px-3 py-2 text-xs font-medium">
        计划设置（只读） · {isDraft ? 'L1草稿，未保存' : 'L1 已保存计划基线'}
      </div>
      <ParameterProjectionList projection={projection ? { ...projection, steps: [] } : undefined} unavailable={unavailable} />
    </section>
  );
}
