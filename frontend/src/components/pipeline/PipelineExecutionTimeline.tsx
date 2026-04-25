import type { PipelineDef, PipelineStep } from '@/utils/api';

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
    return <span className="text-gray-400">empty</span>;
  }
  return (
    <span className="flex flex-wrap gap-1">
      {steps.slice(0, 4).map((step) => (
        <span
          key={step.step_id}
          className={`rounded px-1.5 py-0.5 ${
            step.enabled === false ? 'bg-gray-100 text-gray-400 line-through' : 'bg-white text-gray-700'
          }`}
        >
          {step.step_id}
        </span>
      ))}
      {steps.length > 4 && <span className="text-gray-400">+{steps.length - 4}</span>}
    </span>
  );
}

export default function PipelineExecutionTimeline({
  setupPipeline,
  taskPipeline,
  teardownPipeline,
}: PipelineExecutionTimelineProps) {
  const groups: TimelineGroup[] = [
    { label: 'Setup Prepare', steps: setupPipeline.stages.prepare ?? [], tone: 'border-slate-200 bg-slate-50' },
    { label: 'Task Prepare', steps: taskPipeline.stages.prepare ?? [], tone: 'border-slate-200 bg-slate-50' },
    { label: 'Task Execute', steps: taskPipeline.stages.execute ?? [], tone: 'border-emerald-200 bg-emerald-50' },
    { label: 'Task Post Process', steps: taskPipeline.stages.post_process ?? [], tone: 'border-amber-200 bg-amber-50' },
    { label: 'Teardown Post Process', steps: teardownPipeline.stages.post_process ?? [], tone: 'border-amber-200 bg-amber-50' },
  ];

  return (
    <div className="rounded-xl border border-gray-200 bg-white p-3">
      <div className="mb-2 text-sm font-semibold text-gray-800">执行全景图</div>
      <div className="grid gap-2 xl:grid-cols-5">
        {groups.map((group) => (
          <div key={group.label} className={`min-w-0 rounded-lg border p-2 ${group.tone}`}>
            <div className="flex items-center justify-between gap-2">
              <span className="truncate text-xs font-semibold text-gray-800">{group.label}</span>
              <span className="rounded-full bg-white px-1.5 py-0.5 text-[11px] text-gray-500">
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
