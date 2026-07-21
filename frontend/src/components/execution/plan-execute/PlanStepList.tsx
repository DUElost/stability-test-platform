import { TEXT } from '@/design-system/tokens';
import { cn } from '@/lib/utils';
import {
  groupPlanStepsByStage,
  stageChipClass,
  type PlanStepLike,
} from './planExecuteSteps';

interface PlanStepListProps {
  steps: PlanStepLike[] | null | undefined;
  scriptParamsByKey: Map<string, Record<string, unknown>>;
}

export function PlanStepList({ steps, scriptParamsByKey }: PlanStepListProps) {
  const groups = groupPlanStepsByStage(steps);

  if (groups.length === 0) {
    return (
      <div className={cn('px-3 py-6 text-center text-xs', TEXT.subtitle)}>此 Plan 暂无步骤</div>
    );
  }

  let globalIndex = 0;

  return (
    <div className="max-h-72 overflow-y-auto" data-testid="plan-step-list">
      {groups.map((group) => (
        <div key={group.stage} className="border-b last:border-b-0">
          <div className="sticky top-0 z-[1] flex items-center gap-2 border-b bg-muted/80 px-3 py-1.5 backdrop-blur-sm">
            <span
              className={cn(
                'rounded border px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wider',
                stageChipClass(group.stage),
              )}
            >
              {group.label}
            </span>
            <span className={cn('text-[11px]', TEXT.subtitle)}>{group.steps.length} 步</span>
          </div>
          <div className="divide-y">
            {group.steps.map((step) => {
              globalIndex += 1;
              const index = globalIndex;
              const paramsKey = step.script_name && step.script_version
                ? `${step.script_name}@${step.script_version}`
                : '';
              const defaultParams = paramsKey ? scriptParamsByKey.get(paramsKey) : undefined;
              const paramsJson = defaultParams && Object.keys(defaultParams).length > 0
                ? JSON.stringify(defaultParams, null, 2)
                : null;
              return (
                <details
                  key={step.id ?? step.step_key ?? `${group.stage}-${index}`}
                  className={cn(step.enabled === false && 'opacity-50')}
                >
                  <summary className="grid cursor-pointer grid-cols-[32px_1fr_auto] items-center gap-2 px-3 py-2 text-xs">
                    <span className={TEXT.subtitle}>{index}</span>
                    <span className="truncate">
                      {step.script_name || step.step_key || '未命名步骤'}
                      {step.script_version ? ` · ${step.script_version}` : ''}
                    </span>
                    <span>{step.enabled === false ? '停用' : '启用'}</span>
                  </summary>
                  <div className="border-t bg-muted/30 px-3 py-2">
                    <div className={cn('mb-1 text-[11px]', TEXT.subtitle)}>default_params（只读）</div>
                    {paramsJson ? (
                      <pre className="max-h-40 overflow-auto rounded border bg-background p-2 font-mono text-[11px] leading-5">
                        {paramsJson}
                      </pre>
                    ) : (
                      <div className={cn('text-[11px]', TEXT.subtitle)}>
                        {paramsKey ? '该脚本版本无 default_params 或尚未加载' : '步骤缺少脚本引用'}
                      </div>
                    )}
                  </div>
                </details>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
}
