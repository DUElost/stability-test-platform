import type { PipelineDef, PipelineStep } from '@/utils/api';
import { PANEL, PIPELINE_TIMELINE_TONE, STATUS_CHIP, TEXT } from '@/design-system/tokens';
import { cn } from '@/lib/utils';

interface TimelineGroup {
  label: string;
  steps: PipelineStep[];
  tone: string;
}

interface PipelineExecutionTimelineProps {
  setupPipeline: PipelineDef;
  taskPipeline: PipelineDef;
  teardownPipeline: PipelineDef;
}

function countEnabled(steps: PipelineStep[]): number {
  return steps.filter((step) => step.enabled !== false).length;
}

function StepNames({ steps }: { steps: PipelineStep[] }) {
  if (steps.length === 0) {
    return <span className={TEXT.subtitle}>empty</span>;
  }
  return (
    <span className="flex flex-wrap gap-1">
      {steps.slice(0, 4).map((step) => (
        <span
          key={step.step_id}
          className={cn(
            'rounded px-1.5 py-0.5',
            step.enabled === false
              ? cn(STATUS_CHIP.muted, 'line-through')
              : cn('bg-card', TEXT.body),
          )}
        >
          {step.step_id}
        </span>
      ))}
      {steps.length > 4 && <span className={TEXT.subtitle}>+{steps.length - 4}</span>}
    </span>
  );
}

export default function PipelineExecutionTimeline({
  setupPipeline,
  taskPipeline,
  teardownPipeline,
}: PipelineExecutionTimelineProps) {
  const groups: TimelineGroup[] = [
    { label: 'Setup Init', steps: setupPipeline.lifecycle.init ?? [], tone: PIPELINE_TIMELINE_TONE.neutral },
    { label: 'Task Init', steps: taskPipeline.lifecycle.init ?? [], tone: PIPELINE_TIMELINE_TONE.neutral },
    { label: 'Task Patrol', steps: taskPipeline.lifecycle.patrol?.steps ?? [], tone: PIPELINE_TIMELINE_TONE.patrol },
    { label: 'Task Teardown', steps: taskPipeline.lifecycle.teardown ?? [], tone: PIPELINE_TIMELINE_TONE.teardown },
    { label: 'Teardown', steps: teardownPipeline.lifecycle.teardown ?? [], tone: PIPELINE_TIMELINE_TONE.teardown },
  ];

  return (
    <div className={cn(PANEL.root, 'p-3')}>
      <div className={cn('mb-2 text-sm font-semibold', TEXT.heading)}>执行全景图</div>
      <div className="grid gap-2 xl:grid-cols-5">
        {groups.map((group) => (
          <div key={group.label} className={cn('min-w-0 rounded-lg border p-2', group.tone)}>
            <div className="flex items-center justify-between gap-2">
              <span className={cn('truncate text-xs font-semibold', TEXT.heading)}>{group.label}</span>
              <span className={cn('rounded-full bg-card px-1.5 py-0.5 text-[11px]', TEXT.subtitle)}>
                {countEnabled(group.steps)}/{group.steps.length}
              </span>
            </div>
            <div className="mt-2 min-h-6 text-[11px]">
              <StepNames steps={group.steps} />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
