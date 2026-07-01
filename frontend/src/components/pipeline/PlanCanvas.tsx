import type { PipelineDef, PipelinePhase, PipelineStep, ScriptEntry } from '@/utils/api/types';
import { ArrowDown, ArrowUp, Copy, Trash2 } from 'lucide-react';
import {
  PIPELINE_EDITOR,
  PIPELINE_PHASE_HEAD,
  STATUS_CHIP,
  TEXT,
} from '@/design-system/tokens';
import { cn } from '@/lib/utils';

const PHASE_LABELS: Record<PipelinePhase, string> = {
  init: 'Init',
  patrol: 'Patrol',
  teardown: 'Teardown',
};

interface PlanCanvasProps {
  planName: string;
  onPlanNameChange: (next: string) => void;
  description: string;
  onDescriptionChange: (next: string) => void;
  failureThreshold: number;
  onFailureThresholdChange: (next: number) => void;
  patrolIntervalSeconds: number | null;
  onPatrolIntervalChange: (next: number | null) => void;
  timeoutSeconds: number | null;
  onTimeoutChange: (next: number | null) => void;
  nextPlanName: string | null;
  isCurrentEditing: boolean;
  lifecycle: PipelineDef;
  onLifecycleChange: (next: PipelineDef) => void;
  selectedStepKey: string | null;
  onSelectStep: (key: string | null) => void;
  scripts: ScriptEntry[];
  readOnly?: boolean;
}

function createEmptyStep(scripts: ScriptEntry[], phase: PipelinePhase, index: number): PipelineStep {
  const firstScript = scripts.find(s => s.is_active);
  return {
    step_id: `${phase}_${index + 1}_${Math.random().toString(36).slice(2, 6)}`,
    action: firstScript ? `script:${firstScript.name}` : 'script:',
    version: firstScript?.version ?? '',
    params: {},
    timeout_seconds: 30,
    retry: 0,
    enabled: true,
  };
}

function getPhaseSteps(lifecycle: PipelineDef, phase: PipelinePhase): PipelineStep[] {
  const lc = lifecycle.lifecycle;
  if (phase === 'patrol') return lc.patrol?.steps ?? [];
  return lc[phase] ?? [];
}

function setPhaseSteps(lifecycle: PipelineDef, phase: PipelinePhase, steps: PipelineStep[]): PipelineDef {
  const lc = { ...lifecycle.lifecycle };
  if (phase === 'patrol') {
    if (steps.length === 0) {
      delete (lc as { patrol?: unknown }).patrol;
    } else {
      lc.patrol = {
        interval_seconds: lc.patrol?.interval_seconds ?? 60,
        steps,
      };
    }
  } else {
    if (phase === 'init') lc.init = steps;
    else lc.teardown = steps;
  }
  return { lifecycle: lc };
}

export default function PlanCanvas({
  planName,
  onPlanNameChange,
  description,
  onDescriptionChange,
  failureThreshold,
  onFailureThresholdChange,
  patrolIntervalSeconds,
  onPatrolIntervalChange,
  timeoutSeconds,
  onTimeoutChange,
  nextPlanName,
  isCurrentEditing,
  lifecycle,
  onLifecycleChange,
  selectedStepKey,
  onSelectStep,
  scripts,
  readOnly,
}: PlanCanvasProps) {
  const totalSteps =
    (lifecycle.lifecycle.init?.length ?? 0) +
    (lifecycle.lifecycle.patrol?.steps?.length ?? 0) +
    (lifecycle.lifecycle.teardown?.length ?? 0);

  const handleAddStep = (phase: PipelinePhase) => {
    if (readOnly) return;
    const current = getPhaseSteps(lifecycle, phase);
    const newStep = createEmptyStep(scripts, phase, current.length);
    const next = setPhaseSteps(lifecycle, phase, [...current, newStep]);
    onLifecycleChange(next);
    onSelectStep(newStep.step_id);
  };

  const handleMoveStep = (phase: PipelinePhase, index: number, delta: -1 | 1) => {
    if (readOnly) return;
    const steps = [...getPhaseSteps(lifecycle, phase)];
    const target = index + delta;
    if (target < 0 || target >= steps.length) return;
    const tmp = steps[index];
    steps[index] = steps[target];
    steps[target] = tmp;
    onLifecycleChange(setPhaseSteps(lifecycle, phase, steps));
  };

  const handleDuplicateStep = (phase: PipelinePhase, index: number) => {
    if (readOnly) return;
    const steps = [...getPhaseSteps(lifecycle, phase)];
    const base = steps[index];
    if (!base) return;
    const baseId = base.step_id || `${phase}_${index}`;
    const copy: PipelineStep = {
      ...base,
      step_id: `${baseId}_copy_${Math.random().toString(36).slice(2, 5)}`,
    };
    steps.splice(index + 1, 0, copy);
    onLifecycleChange(setPhaseSteps(lifecycle, phase, steps));
    onSelectStep(copy.step_id);
  };

  const handleRemoveStep = (phase: PipelinePhase, index: number) => {
    if (readOnly) return;
    const steps = [...getPhaseSteps(lifecycle, phase)];
    const removed = steps[index];
    steps.splice(index, 1);
    onLifecycleChange(setPhaseSteps(lifecycle, phase, steps));
    if (selectedStepKey && removed?.step_id === selectedStepKey) {
      onSelectStep(null);
    }
  };

  return (
    <section className={cn('flex-1 min-h-0 min-w-0 overflow-y-auto', PIPELINE_EDITOR.canvasBg)}>
      <div className="p-3.5 grid gap-3">
        <PlanHeader
          planName={planName}
          onPlanNameChange={onPlanNameChange}
          description={description}
          onDescriptionChange={onDescriptionChange}
          failureThreshold={failureThreshold}
          onFailureThresholdChange={onFailureThresholdChange}
          patrolIntervalSeconds={patrolIntervalSeconds}
          onPatrolIntervalChange={onPatrolIntervalChange}
          timeoutSeconds={timeoutSeconds}
          onTimeoutChange={onTimeoutChange}
          totalSteps={totalSteps}
          nextPlanName={nextPlanName}
          isCurrentEditing={isCurrentEditing}
          readOnly={readOnly}
        />

        {(['init', 'patrol', 'teardown'] as PipelinePhase[]).map(phase => {
          const steps = getPhaseSteps(lifecycle, phase);
          return (
            <PhaseSection
              key={phase}
              phase={phase}
              steps={steps}
              selectedStepKey={selectedStepKey}
              onSelectStep={onSelectStep}
              onAddStep={() => handleAddStep(phase)}
              onMoveStep={(idx, delta) => handleMoveStep(phase, idx, delta)}
              onDuplicateStep={idx => handleDuplicateStep(phase, idx)}
              onRemoveStep={idx => handleRemoveStep(phase, idx)}
              patrolIntervalSeconds={phase === 'patrol' ? patrolIntervalSeconds : null}
              readOnly={readOnly}
            />
          );
        })}
      </div>
    </section>
  );
}

interface PlanHeaderProps {
  planName: string;
  onPlanNameChange: (next: string) => void;
  description: string;
  onDescriptionChange: (next: string) => void;
  failureThreshold: number;
  onFailureThresholdChange: (next: number) => void;
  patrolIntervalSeconds: number | null;
  onPatrolIntervalChange: (next: number | null) => void;
  timeoutSeconds: number | null;
  onTimeoutChange: (next: number | null) => void;
  totalSteps: number;
  nextPlanName: string | null;
  isCurrentEditing: boolean;
  readOnly?: boolean;
}

function PlanHeader({
  planName,
  onPlanNameChange,
  description,
  onDescriptionChange,
  failureThreshold,
  onFailureThresholdChange,
  patrolIntervalSeconds,
  onPatrolIntervalChange,
  timeoutSeconds,
  onTimeoutChange,
  totalSteps,
  nextPlanName,
  isCurrentEditing,
  readOnly,
}: PlanHeaderProps) {
  const metaInputCls = cn('h-6 px-2 text-xs', PIPELINE_EDITOR.inputInline);

  return (
    <div className={cn('px-4 py-3.5 grid gap-2.5', PIPELINE_EDITOR.card)}>
      <div className="flex items-center justify-between gap-4">
        <input
          type="text"
          value={planName}
          onChange={e => onPlanNameChange(e.target.value)}
          placeholder="Plan 名称"
          readOnly={readOnly}
          className={cn(
            'flex-1 min-w-0 text-lg font-extrabold tracking-tight',
            PIPELINE_EDITOR.inputTitle,
          )}
        />
        {isCurrentEditing && (
          <span className={cn('shrink-0 inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-bold', STATUS_CHIP.primary)}>
            当前编辑
          </span>
        )}
      </div>

      <input
        type="text"
        value={description}
        onChange={e => onDescriptionChange(e.target.value)}
        placeholder="Plan 描述（可选）"
        readOnly={readOnly}
        className={cn('text-[12px]', PIPELINE_EDITOR.inputTitle, TEXT.subtitle)}
      />

      <div className={cn('flex flex-wrap items-center gap-x-4 gap-y-2 text-xs', TEXT.subtitle)}>
        <MetaItem label="Patrol 间隔">
          <input
            type="number"
            min={5}
            value={patrolIntervalSeconds ?? ''}
            placeholder="不开启"
            disabled={readOnly}
            onChange={e => {
              const raw = e.target.value;
              if (raw === '') onPatrolIntervalChange(null);
              else onPatrolIntervalChange(Math.max(5, parseInt(raw, 10) || 60));
            }}
            className={cn('w-24', metaInputCls)}
          />
          <span className="text-[11px]">秒</span>
        </MetaItem>

        <MetaItem label="失败阈值">
          <input
            type="number"
            min={0}
            max={1}
            step={0.01}
            value={failureThreshold}
            disabled={readOnly}
            onChange={e => onFailureThresholdChange(Math.min(1, Math.max(0, parseFloat(e.target.value) || 0)))}
            className={cn('w-20', metaInputCls)}
          />
          <span className={cn('text-[11px] font-semibold', TEXT.body)}>{Math.round(failureThreshold * 100)}%</span>
        </MetaItem>

        <MetaItem label="全局超时">
          <input
            type="number"
            min={0}
            value={timeoutSeconds ?? ''}
            placeholder="不限"
            disabled={readOnly}
            onChange={e => {
              const raw = e.target.value;
              if (raw === '') onTimeoutChange(null);
              else onTimeoutChange(Math.max(0, parseInt(raw, 10) || 0));
            }}
            className={cn('w-24', metaInputCls)}
          />
          <span className="text-[11px]">秒</span>
        </MetaItem>

        <MetaItem label="总步骤">
          <span className={cn('text-[12px] font-semibold', TEXT.body)}>{totalSteps}</span>
        </MetaItem>
      </div>

      {nextPlanName && (
        <div className={cn('flex items-center gap-1.5 text-[11px] px-2.5 py-1.5 rounded-md', STATUS_CHIP.primary)}>
          完成 <strong className="font-semibold">{planName || '当前 Plan'}</strong> 后 → 自动执行{' '}
          <strong className="font-semibold">{nextPlanName}</strong>
        </div>
      )}
    </div>
  );
}

function MetaItem({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-1.5">
      <span className={cn('text-[11px] font-bold uppercase tracking-wide', TEXT.subtitle)}>{label}</span>
      {children}
    </div>
  );
}

interface PhaseSectionProps {
  phase: PipelinePhase;
  steps: PipelineStep[];
  selectedStepKey: string | null;
  onSelectStep: (key: string | null) => void;
  onAddStep: () => void;
  onMoveStep: (idx: number, delta: -1 | 1) => void;
  onDuplicateStep: (idx: number) => void;
  onRemoveStep: (idx: number) => void;
  patrolIntervalSeconds: number | null;
  readOnly?: boolean;
}

function PhaseSection({
  phase,
  steps,
  selectedStepKey,
  onSelectStep,
  onAddStep,
  onMoveStep,
  onDuplicateStep,
  onRemoveStep,
  patrolIntervalSeconds,
  readOnly,
}: PhaseSectionProps) {
  const subTitle =
    phase === 'init'
      ? `一次性初始化 · ${steps.length} ${steps.length === 1 ? 'Step' : 'Steps'}`
      : phase === 'patrol'
      ? `↻ 每 ${patrolIntervalSeconds ?? 60}s 循环 · ${steps.length} ${steps.length === 1 ? 'Step' : 'Steps'}`
      : `收尾清理 · ${steps.length} ${steps.length === 1 ? 'Step' : 'Steps'}`;

  return (
    <div className={cn('overflow-hidden', PIPELINE_EDITOR.card)}>
      <div
        className={cn(
          'px-4 py-2.5 flex items-center justify-between gap-2.5 text-sm font-bold border-b border-border',
          PIPELINE_PHASE_HEAD[phase],
        )}
      >
        <span>{PHASE_LABELS[phase]}</span>
        <span className="text-[12px] font-medium opacity-70">{subTitle}</span>
      </div>
      <div className="px-4 py-2.5 grid gap-1.5">
        {steps.map((step, idx) => (
          <StepRow
            key={step.step_id || `${phase}_${idx}`}
            phase={phase}
            step={step}
            index={idx}
            total={steps.length}
            selected={!!step.step_id && step.step_id === selectedStepKey}
            onSelect={() => onSelectStep(step.step_id)}
            onMove={delta => onMoveStep(idx, delta)}
            onDuplicate={() => onDuplicateStep(idx)}
            onRemove={() => onRemoveStep(idx)}
            readOnly={readOnly}
          />
        ))}
        {!readOnly && (
          <button
            type="button"
            onClick={onAddStep}
            className={cn(
              'w-full min-h-[34px] px-2 grid place-items-center text-[11px] rounded-md border transition',
              PIPELINE_EDITOR.addStepBtn,
            )}
          >
            + 添加 {PHASE_LABELS[phase]} 步骤
          </button>
        )}
      </div>
    </div>
  );
}

interface StepRowProps {
  phase: PipelinePhase;
  step: PipelineStep;
  index: number;
  total: number;
  selected: boolean;
  onSelect: () => void;
  onMove: (delta: -1 | 1) => void;
  onDuplicate: () => void;
  onRemove: () => void;
  readOnly?: boolean;
}

function StepRow({
  phase,
  step,
  index,
  total,
  selected,
  onSelect,
  onMove,
  onDuplicate,
  onRemove,
  readOnly,
}: StepRowProps) {
  const scriptName = step.action?.startsWith('script:') ? step.action.slice(7) : step.action || '—';

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onSelect}
      onKeyDown={e => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          onSelect();
        }
      }}
      className={cn(
        'grid grid-cols-[24px_1fr_auto] gap-2.5 items-center px-2.5 py-2 rounded-md border transition cursor-pointer',
        selected ? PIPELINE_EDITOR.stepSelected : PIPELINE_EDITOR.stepIdle,
      )}
    >
      <div
        title={`${phase} #${index + 1}`}
        className={cn(
          'w-6 h-6 rounded-[5px] grid place-items-center text-[11px] font-extrabold',
          PIPELINE_EDITOR.stepIndex,
        )}
      >
        {index + 1}
      </div>

      <div className="min-w-0">
        <div className={cn('flex items-center gap-1.5 text-[13px] font-bold', TEXT.heading)}>
          <span className="truncate">{scriptName}</span>
          {step.version && (
            <span className={cn('text-[10px] font-mono', TEXT.subtitle)}>v{step.version.replace(/^v/, '')}</span>
          )}
          {step.enabled === false && (
            <span className={cn('ml-1 inline-flex items-center px-1.5 py-px rounded-full text-[10px] font-bold', STATUS_CHIP.muted)}>
              已禁用
            </span>
          )}
        </div>
        <div className={cn('mt-0.5 flex flex-wrap items-center gap-x-2.5 gap-y-0.5 text-[11px]', TEXT.subtitle)}>
          <span className="font-mono">{step.action || '—'}</span>
          <span>{step.timeout_seconds != null ? `${step.timeout_seconds}s` : '∞'}</span>
          <span>retry {step.retry ?? 0}</span>
        </div>
      </div>

      <div className="flex items-center gap-1" onClick={e => e.stopPropagation()}>
        <IconBtn label="上移" disabled={readOnly || index === 0} onClick={() => onMove(-1)}>
          <ArrowUp className="w-3.5 h-3.5" />
        </IconBtn>
        <IconBtn label="下移" disabled={readOnly || index === total - 1} onClick={() => onMove(1)}>
          <ArrowDown className="w-3.5 h-3.5" />
        </IconBtn>
        <IconBtn label="复制" disabled={readOnly} onClick={onDuplicate}>
          <Copy className="w-3.5 h-3.5" />
        </IconBtn>
        <IconBtn label="删除" tone="danger" disabled={readOnly} onClick={onRemove}>
          <Trash2 className="w-3.5 h-3.5" />
        </IconBtn>
      </div>
    </div>
  );
}

function IconBtn({
  label,
  onClick,
  disabled,
  tone,
  children,
}: {
  label: string;
  onClick: () => void;
  disabled?: boolean;
  tone?: 'danger';
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      title={label}
      aria-label={label}
      disabled={disabled}
      onClick={onClick}
      className={cn(
        'w-6 h-6 grid place-items-center rounded-[5px] transition',
        PIPELINE_EDITOR.iconBtn,
        tone === 'danger' && PIPELINE_EDITOR.iconBtnDanger,
      )}
    >
      {children}
    </button>
  );
}
