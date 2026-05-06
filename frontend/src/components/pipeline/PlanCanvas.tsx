import type { PipelineDef, PipelinePhase, PipelineStep, ScriptEntry } from '@/utils/api/types';
import { ArrowDown, ArrowUp, Copy, Trash2 } from 'lucide-react';

const PHASE_LABELS: Record<PipelinePhase, string> = {
  init: 'Init',
  patrol: 'Patrol',
  teardown: 'Teardown',
};

const PHASE_HEAD_TONE: Record<PipelinePhase, string> = {
  init: 'bg-slate-50 text-slate-700',
  patrol: 'bg-emerald-50 text-emerald-800',
  teardown: 'bg-amber-50 text-amber-800',
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
      delete (lc as any).patrol;
    } else {
      lc.patrol = {
        interval_seconds: lc.patrol?.interval_seconds ?? 60,
        steps,
      };
    }
  } else {
    (lc as any)[phase] = steps;
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
    <section className="flex-1 min-w-0 bg-slate-50 overflow-y-auto">
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
  return (
    <div className="bg-white border border-slate-200 rounded-[10px] px-4 py-3.5 grid gap-2.5 shadow-[0_4px_12px_rgba(15,23,42,.03)]">
      <div className="flex items-center justify-between gap-4">
        <input
          type="text"
          value={planName}
          onChange={e => onPlanNameChange(e.target.value)}
          placeholder="Plan 名称"
          readOnly={readOnly}
          className="flex-1 min-w-0 text-lg font-extrabold tracking-tight bg-transparent border-0 border-b border-transparent focus:border-cyan-500 focus:outline-none placeholder:text-slate-300"
        />
        {isCurrentEditing && (
          <span className="shrink-0 inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-bold bg-indigo-50 border border-indigo-200 text-indigo-700">
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
        className="text-[12px] text-slate-500 bg-transparent border-0 border-b border-transparent focus:border-cyan-300 focus:outline-none placeholder:text-slate-300"
      />

      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-slate-500">
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
            className="w-24 h-6 px-2 text-xs border border-slate-300 rounded-[5px] bg-white text-slate-700 focus:outline-none focus:ring-1 focus:ring-cyan-500"
          />
          <span className="text-[11px] text-slate-400">秒</span>
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
            className="w-20 h-6 px-2 text-xs border border-slate-300 rounded-[5px] bg-white text-slate-700 focus:outline-none focus:ring-1 focus:ring-cyan-500"
          />
          <span className="text-[11px] font-semibold text-slate-700">{Math.round(failureThreshold * 100)}%</span>
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
            className="w-24 h-6 px-2 text-xs border border-slate-300 rounded-[5px] bg-white text-slate-700 focus:outline-none focus:ring-1 focus:ring-cyan-500"
          />
          <span className="text-[11px] text-slate-400">秒</span>
        </MetaItem>

        <MetaItem label="总步骤">
          <span className="text-[12px] font-semibold text-slate-700">{totalSteps}</span>
        </MetaItem>
      </div>

      {nextPlanName && (
        <div className="flex items-center gap-1.5 text-[11px] px-2.5 py-1.5 bg-violet-50 rounded-md text-violet-700">
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
      <span className="text-[11px] font-bold uppercase tracking-wide text-slate-400">{label}</span>
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
    <div className="bg-white border border-slate-200 rounded-[10px] overflow-hidden shadow-[0_4px_12px_rgba(15,23,42,.03)]">
      <div className={`px-4 py-2.5 flex items-center justify-between gap-2.5 text-sm font-bold border-b border-slate-100 ${PHASE_HEAD_TONE[phase]}`}>
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
            className="w-full min-h-[34px] px-2 grid place-items-center text-[11px] text-slate-500 border border-dashed border-slate-300 rounded-md hover:border-cyan-500 hover:text-cyan-700 hover:bg-cyan-50/40 transition"
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
      className={[
        'grid grid-cols-[24px_1fr_auto] gap-2.5 items-center px-2.5 py-2 rounded-md border transition cursor-pointer',
        selected
          ? 'border-cyan-300 bg-cyan-50/60 shadow-[0_0_0_2px_rgba(14,116,144,.08)]'
          : 'border-slate-200 bg-white hover:border-slate-300',
      ].join(' ')}
    >
      <div
        title={`${phase} #${index + 1}`}
        className="w-6 h-6 rounded-[5px] grid place-items-center text-[11px] font-extrabold bg-slate-100 text-slate-500"
      >
        {index + 1}
      </div>

      <div className="min-w-0">
        <div className="flex items-center gap-1.5 text-[13px] font-bold text-slate-800">
          <span className="truncate">{scriptName}</span>
          {step.version && (
            <span className="text-[10px] font-mono text-slate-400">v{step.version.replace(/^v/, '')}</span>
          )}
          {step.enabled === false && (
            <span className="ml-1 inline-flex items-center px-1.5 py-px rounded-full text-[10px] font-bold bg-slate-100 text-slate-500 border border-slate-200">
              已禁用
            </span>
          )}
        </div>
        <div className="mt-0.5 flex flex-wrap items-center gap-x-2.5 gap-y-0.5 text-[11px] text-slate-500">
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
      className={[
        'w-6 h-6 grid place-items-center rounded-[5px] border border-slate-200 bg-white text-slate-500 transition',
        'hover:bg-slate-100 hover:text-slate-700',
        tone === 'danger' ? 'hover:bg-red-50 hover:text-red-600 hover:border-red-200' : '',
        'disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:bg-white disabled:hover:text-slate-500 disabled:hover:border-slate-200',
      ].join(' ')}
    >
      {children}
    </button>
  );
}
