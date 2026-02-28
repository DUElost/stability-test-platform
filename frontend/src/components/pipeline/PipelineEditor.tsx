import React, { useState, useCallback } from 'react';
import {
  Plus, Trash2, GripVertical, ChevronDown, ChevronRight,
  Layers, Zap, Copy, Code2,
} from 'lucide-react';
import {
  DndContext, closestCenter, KeyboardSensor, PointerSensor,
  useSensor, useSensors, DragEndEvent,
} from '@dnd-kit/core';
import {
  SortableContext, sortableKeyboardCoordinates,
  verticalListSortingStrategy, useSortable, arrayMove,
} from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';

import type { PipelineDef, PipelinePhase, PipelineStep } from './pipelineTypes';
import { createEmptyPhase, createEmptyStep, getActionName, getActionPrefix } from './pipelineTypes';
import { BUILTIN_ACTIONS, ACTION_CATEGORIES, getActionDef } from './actionCatalog';
import type { ParamSchema } from '../task/DynamicToolForm';
import { DynamicToolForm } from '../task/DynamicToolForm';

// ─── Props ────────────────────────────────────────────────────────────────────

interface PipelineEditorProps {
  value: PipelineDef;
  onChange: (def: PipelineDef) => void;
  readOnly?: boolean;
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

function cloneDeep<T>(v: T): T { return JSON.parse(JSON.stringify(v)); }

// ─── Sortable Step Item ──────────────────────────────────────────────────────

interface SortableStepProps {
  id: string;
  step: PipelineStep;
  stepIndex: number;
  phaseIndex: number;
  expanded: boolean;
  onToggle: () => void;
  onUpdate: (step: PipelineStep) => void;
  onRemove: () => void;
  onDuplicate: () => void;
  readOnly?: boolean;
}

function SortableStepItem({
  id, step, stepIndex, expanded, onToggle,
  onUpdate, onRemove, onDuplicate, readOnly,
}: SortableStepProps) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id });
  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.5 : 1,
  };

  const prefix = getActionPrefix(step.action);
  const actionName = getActionName(step.action);
  const actionDef = prefix === 'builtin' ? getActionDef(actionName) : undefined;

  const handleFieldChange = (field: keyof PipelineStep, value: any) => {
    onUpdate({ ...step, [field]: value });
  };

  const handleParamChange = (key: string, value: any) => {
    onUpdate({ ...step, params: { ...step.params, [key]: value } });
  };

  const handleActionChange = (newAction: string) => {
    const newPrefix = getActionPrefix(newAction);
    const newName = getActionName(newAction);
    const newDef = newPrefix === 'builtin' ? getActionDef(newName) : undefined;

    // Build default params from action schema
    const newParams: Record<string, any> = {};
    if (newDef) {
      for (const [key, field] of Object.entries(newDef.paramSchema)) {
        if (field.default !== undefined) {
          newParams[key] = field.default;
        }
      }
    }

    onUpdate({
      ...step,
      action: newAction,
      name: step.name || newName,
      params: newParams,
    });
  };

  // Determine param schema for the current action
  let currentParamSchema: ParamSchema = {};
  if (prefix === 'builtin' && actionDef) {
    currentParamSchema = actionDef.paramSchema;
  }

  return (
    <div ref={setNodeRef} style={style} className="border border-slate-200 rounded-lg bg-white">
      {/* Step header */}
      <div className="flex items-center gap-2 px-3 py-2 cursor-pointer select-none" onClick={onToggle}>
        {!readOnly && (
          <button type="button" {...attributes} {...listeners} className="cursor-grab text-slate-300 hover:text-slate-500 touch-none" onClick={(e) => e.stopPropagation()}>
            <GripVertical size={14} />
          </button>
        )}
        {expanded ? <ChevronDown size={14} className="text-slate-400" /> : <ChevronRight size={14} className="text-slate-400" />}
        <span className="text-xs font-mono text-slate-400 w-5">{stepIndex + 1}</span>
        <span className="text-sm font-medium text-slate-700 flex-1 truncate">{step.name || '(unnamed)'}</span>
        <span className="text-xs text-slate-400 bg-slate-100 px-1.5 py-0.5 rounded font-mono">{step.action}</span>
        {step.on_failure !== 'stop' && (
          <span className={`text-[10px] px-1 py-0.5 rounded ${step.on_failure === 'continue' ? 'bg-amber-100 text-amber-700' : 'bg-blue-100 text-blue-700'}`}>
            {step.on_failure}
          </span>
        )}
        {!readOnly && (
          <div className="flex items-center gap-1 ml-1">
            <button type="button" className="text-slate-300 hover:text-indigo-500 p-0.5" onClick={(e) => { e.stopPropagation(); onDuplicate(); }} title="Duplicate">
              <Copy size={13} />
            </button>
            <button type="button" className="text-slate-300 hover:text-red-500 p-0.5" onClick={(e) => { e.stopPropagation(); onRemove(); }} title="Remove">
              <Trash2 size={13} />
            </button>
          </div>
        )}
      </div>

      {/* Step details (expanded) */}
      {expanded && (
        <div className="px-4 pb-4 pt-1 border-t border-slate-100 space-y-3">
          {/* Row 1: Name + Action */}
          <div className="grid grid-cols-2 gap-3">
            <div className="flex flex-col gap-1">
              <label className="text-xs font-medium text-slate-600">Name</label>
              <input
                type="text"
                value={step.name}
                onChange={(e) => handleFieldChange('name', e.target.value)}
                className="w-full border border-slate-300 rounded px-2.5 py-1.5 text-sm focus:ring-2 focus:ring-indigo-500 outline-none"
                placeholder="Step name"
                readOnly={readOnly}
              />
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-xs font-medium text-slate-600">Action</label>
              <ActionSelector
                value={step.action}
                onChange={handleActionChange}
                disabled={readOnly}
              />
            </div>
          </div>

          {/* Row 2: Timeout, On Failure, Max Retries */}
          <div className="grid grid-cols-3 gap-3">
            <div className="flex flex-col gap-1">
              <label className="text-xs font-medium text-slate-600">Timeout (s)</label>
              <input
                type="number"
                value={step.timeout}
                min={1}
                onChange={(e) => handleFieldChange('timeout', Number(e.target.value) || 300)}
                className="w-full border border-slate-300 rounded px-2.5 py-1.5 text-sm focus:ring-2 focus:ring-indigo-500 outline-none"
                readOnly={readOnly}
              />
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-xs font-medium text-slate-600">On Failure</label>
              <select
                value={step.on_failure}
                onChange={(e) => handleFieldChange('on_failure', e.target.value)}
                className="w-full border border-slate-300 rounded px-2.5 py-1.5 text-sm focus:ring-2 focus:ring-indigo-500 outline-none bg-white"
                disabled={readOnly}
              >
                <option value="stop">Stop</option>
                <option value="continue">Continue</option>
                <option value="retry">Retry</option>
              </select>
            </div>
            {step.on_failure === 'retry' && (
              <div className="flex flex-col gap-1">
                <label className="text-xs font-medium text-slate-600">Max Retries</label>
                <input
                  type="number"
                  value={step.max_retries}
                  min={0}
                  max={10}
                  onChange={(e) => handleFieldChange('max_retries', Number(e.target.value) || 0)}
                  className="w-full border border-slate-300 rounded px-2.5 py-1.5 text-sm focus:ring-2 focus:ring-indigo-500 outline-none"
                  readOnly={readOnly}
                />
              </div>
            )}
          </div>

          {/* Row 3: Parameters */}
          {Object.keys(currentParamSchema).length > 0 && (
            <div>
              <label className="text-xs font-medium text-slate-600 mb-1 block">Parameters</label>
              <div className="bg-slate-50 p-3 rounded border border-slate-200">
                <DynamicToolForm
                  schema={currentParamSchema}
                  values={step.params}
                  onChange={handleParamChange}
                />
              </div>
            </div>
          )}

          {/* Shell action: show command input */}
          {prefix === 'shell' && (
            <div>
              <label className="text-xs font-medium text-slate-600 mb-1 block">Shell Command</label>
              <input
                type="text"
                value={getActionName(step.action)}
                onChange={(e) => handleFieldChange('action', `shell:${e.target.value}`)}
                className="w-full border border-slate-300 rounded px-2.5 py-1.5 text-sm font-mono focus:ring-2 focus:ring-indigo-500 outline-none"
                placeholder="adb shell ..."
                readOnly={readOnly}
              />
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ─── Action Selector ─────────────────────────────────────────────────────────

interface ActionSelectorProps {
  value: string;
  onChange: (action: string) => void;
  disabled?: boolean;
}

function ActionSelector({ value, onChange, disabled }: ActionSelectorProps) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="w-full border border-slate-300 rounded px-2.5 py-1.5 text-sm focus:ring-2 focus:ring-indigo-500 outline-none bg-white"
      disabled={disabled}
    >
      {ACTION_CATEGORIES.map((cat) => {
        const actions = BUILTIN_ACTIONS.filter((a) => a.category === cat.key);
        if (actions.length === 0) return null;
        return (
          <optgroup key={cat.key} label={cat.label}>
            {actions.map((a) => (
              <option key={a.name} value={`builtin:${a.name}`}>
                {a.label}
              </option>
            ))}
          </optgroup>
        );
      })}
      <optgroup label="Custom">
        <option value="shell:">Shell Command</option>
      </optgroup>
    </select>
  );
}

// ─── Phase Card ──────────────────────────────────────────────────────────────

interface PhaseCardProps {
  phase: PipelinePhase;
  phaseIndex: number;
  totalPhases: number;
  expandedSteps: Set<string>;
  onToggleStep: (key: string) => void;
  onUpdate: (phase: PipelinePhase) => void;
  onRemove: () => void;
  onDuplicate: () => void;
  readOnly?: boolean;
}

function PhaseCard({
  phase, phaseIndex, expandedSteps, onToggleStep,
  onUpdate, onRemove, onDuplicate, readOnly, totalPhases,
}: PhaseCardProps) {
  const [collapsed, setCollapsed] = useState(false);

  const stepIds = phase.steps.map((_, i) => `phase-${phaseIndex}-step-${i}`);

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );

  const handleStepDragEnd = (event: DragEndEvent) => {
    const { active, over } = event;
    if (!over || active.id === over.id) return;
    const oldIndex = stepIds.indexOf(String(active.id));
    const newIndex = stepIds.indexOf(String(over.id));
    if (oldIndex < 0 || newIndex < 0) return;
    onUpdate({ ...phase, steps: arrayMove([...phase.steps], oldIndex, newIndex) });
  };

  const updateStep = (stepIndex: number, step: PipelineStep) => {
    const newSteps = [...phase.steps];
    newSteps[stepIndex] = step;
    onUpdate({ ...phase, steps: newSteps });
  };

  const removeStep = (stepIndex: number) => {
    const newSteps = phase.steps.filter((_, i) => i !== stepIndex);
    onUpdate({ ...phase, steps: newSteps });
  };

  const duplicateStep = (stepIndex: number) => {
    const newSteps = [...phase.steps];
    const dup = cloneDeep(phase.steps[stepIndex]);
    dup.name = `${dup.name}_copy`;
    newSteps.splice(stepIndex + 1, 0, dup);
    onUpdate({ ...phase, steps: newSteps });
  };

  const addStep = () => {
    const newStep = createEmptyStep(`step_${phase.steps.length + 1}`);
    onUpdate({ ...phase, steps: [...phase.steps, newStep] });
  };

  return (
    <div className="border border-slate-200 rounded-lg bg-white shadow-sm">
      {/* Phase header */}
      <div className="flex items-center gap-2 px-4 py-3 bg-slate-50 rounded-t-lg border-b border-slate-200">
        <button type="button" className="text-slate-400 hover:text-slate-600" onClick={() => setCollapsed(!collapsed)}>
          {collapsed ? <ChevronRight size={16} /> : <ChevronDown size={16} />}
        </button>
        <Layers size={16} className="text-indigo-500" />
        {readOnly ? (
          <span className="text-sm font-semibold text-slate-700">{phase.name}</span>
        ) : (
          <input
            type="text"
            value={phase.name}
            onChange={(e) => onUpdate({ ...phase, name: e.target.value })}
            className="text-sm font-semibold text-slate-700 bg-transparent border-none outline-none focus:ring-0 px-0 w-32"
            placeholder="Phase name"
          />
        )}
        <span className="text-xs text-slate-400">({phase.steps.length} step{phase.steps.length !== 1 ? 's' : ''})</span>

        <div className="flex-1" />

        {/* Parallel toggle */}
        <label className="flex items-center gap-1.5 cursor-pointer">
          <input
            type="checkbox"
            checked={phase.parallel}
            onChange={(e) => onUpdate({ ...phase, parallel: e.target.checked })}
            className="h-3.5 w-3.5 rounded border-slate-300 text-indigo-600 focus:ring-indigo-500"
            disabled={readOnly}
          />
          <span className="text-xs text-slate-500">Parallel</span>
        </label>

        {!readOnly && (
          <div className="flex items-center gap-1 ml-2">
            <button type="button" className="text-slate-300 hover:text-indigo-500 p-1" onClick={onDuplicate} title="Duplicate Phase">
              <Copy size={14} />
            </button>
            <button type="button" className="text-slate-300 hover:text-red-500 p-1" onClick={onRemove} title="Remove Phase" disabled={totalPhases <= 1}>
              <Trash2 size={14} />
            </button>
          </div>
        )}
      </div>

      {/* Steps */}
      {!collapsed && (
        <div className="p-3 space-y-2">
          <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleStepDragEnd}>
            <SortableContext items={stepIds} strategy={verticalListSortingStrategy}>
              {phase.steps.map((step, si) => {
                const stepKey = `phase-${phaseIndex}-step-${si}`;
                return (
                  <SortableStepItem
                    key={stepKey}
                    id={stepKey}
                    step={step}
                    stepIndex={si}
                    phaseIndex={phaseIndex}
                    expanded={expandedSteps.has(stepKey)}
                    onToggle={() => onToggleStep(stepKey)}
                    onUpdate={(s) => updateStep(si, s)}
                    onRemove={() => removeStep(si)}
                    onDuplicate={() => duplicateStep(si)}
                    readOnly={readOnly}
                  />
                );
              })}
            </SortableContext>
          </DndContext>

          {!readOnly && (
            <button
              type="button"
              onClick={addStep}
              className="w-full flex items-center justify-center gap-1.5 py-2 text-xs text-slate-400 hover:text-indigo-600 hover:bg-indigo-50 border border-dashed border-slate-200 hover:border-indigo-300 rounded-lg transition-colors"
            >
              <Plus size={13} /> Add Step
            </button>
          )}
        </div>
      )}
    </div>
  );
}

// ─── JSON Preview Panel ──────────────────────────────────────────────────────

interface JsonPreviewProps {
  pipeline: PipelineDef;
  validationErrors: string[];
}

function JsonPreview({ pipeline, validationErrors }: JsonPreviewProps) {
  const json = JSON.stringify(pipeline, null, 2);

  return (
    <div className="border border-slate-200 rounded-lg bg-white shadow-sm">
      <div className="flex items-center gap-2 px-4 py-2 bg-slate-50 border-b border-slate-200 rounded-t-lg">
        <Code2 size={14} className="text-slate-500" />
        <span className="text-xs font-semibold text-slate-500 uppercase tracking-wider">Pipeline JSON</span>
        {validationErrors.length > 0 && (
          <span className="text-[10px] px-1.5 py-0.5 bg-red-100 text-red-600 rounded-full font-medium">
            {validationErrors.length} error{validationErrors.length !== 1 ? 's' : ''}
          </span>
        )}
      </div>
      {validationErrors.length > 0 && (
        <div className="px-4 py-2 bg-red-50 border-b border-red-100">
          {validationErrors.map((err, i) => (
            <p key={i} className="text-xs text-red-600">{err}</p>
          ))}
        </div>
      )}
      <pre className="p-4 text-xs font-mono text-slate-700 overflow-auto max-h-[500px] leading-relaxed">
        {json}
      </pre>
    </div>
  );
}

// ─── Pipeline Editor (Main) ──────────────────────────────────────────────────

export const PipelineEditor: React.FC<PipelineEditorProps> = ({
  value,
  onChange,
  readOnly,
}) => {
  const [expandedSteps, setExpandedSteps] = useState<Set<string>>(new Set());
  const [showJson, setShowJson] = useState(false);

  const toggleStep = useCallback((key: string) => {
    setExpandedSteps((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });
  }, []);

  // Phase-level DnD
  const phaseSensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 8 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );
  const phaseIds = value.phases.map((_, i) => `phase-${i}`);

  const handlePhaseDragEnd = (event: DragEndEvent) => {
    const { active, over } = event;
    if (!over || active.id === over.id) return;
    const oldIndex = phaseIds.indexOf(String(active.id));
    const newIndex = phaseIds.indexOf(String(over.id));
    if (oldIndex < 0 || newIndex < 0) return;
    onChange({ ...value, phases: arrayMove([...value.phases], oldIndex, newIndex) });
  };

  const updatePhase = (phaseIndex: number, phase: PipelinePhase) => {
    const phases = [...value.phases];
    phases[phaseIndex] = phase;
    onChange({ ...value, phases });
  };

  const removePhase = (phaseIndex: number) => {
    if (value.phases.length <= 1) return;
    onChange({ ...value, phases: value.phases.filter((_, i) => i !== phaseIndex) });
  };

  const duplicatePhase = (phaseIndex: number) => {
    const phases = [...value.phases];
    const dup = cloneDeep(phases[phaseIndex]);
    dup.name = `${dup.name}_copy`;
    phases.splice(phaseIndex + 1, 0, dup);
    onChange({ ...value, phases });
  };

  const addPhase = () => {
    const name = `phase_${value.phases.length + 1}`;
    onChange({ ...value, phases: [...value.phases, createEmptyPhase(name)] });
  };

  // Validation
  const validationErrors = validatePipeline(value);

  return (
    <div className="space-y-4">
      {/* Toolbar */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Zap size={16} className="text-indigo-500" />
          <span className="text-sm font-semibold text-slate-600">
            {value.phases.length} phase{value.phases.length !== 1 ? 's' : ''}, {value.phases.reduce((s, p) => s + p.steps.length, 0)} steps
          </span>
        </div>
        <button
          type="button"
          onClick={() => setShowJson(!showJson)}
          className={`flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-md border transition-colors ${showJson ? 'bg-indigo-50 border-indigo-300 text-indigo-600' : 'border-slate-200 text-slate-500 hover:border-indigo-300 hover:text-indigo-600'}`}
        >
          <Code2 size={13} /> JSON
        </button>
      </div>

      <div className={showJson ? 'grid grid-cols-1 lg:grid-cols-2 gap-4' : ''}>
        {/* Phase list */}
        <div className="space-y-3">
          <DndContext sensors={phaseSensors} collisionDetection={closestCenter} onDragEnd={handlePhaseDragEnd}>
            <SortableContext items={phaseIds} strategy={verticalListSortingStrategy}>
              {value.phases.map((phase, pi) => (
                <SortablePhaseWrapper key={`phase-${pi}`} id={`phase-${pi}`} readOnly={readOnly}>
                  <PhaseCard
                    phase={phase}
                    phaseIndex={pi}
                    totalPhases={value.phases.length}
                    expandedSteps={expandedSteps}
                    onToggleStep={toggleStep}
                    onUpdate={(p) => updatePhase(pi, p)}
                    onRemove={() => removePhase(pi)}
                    onDuplicate={() => duplicatePhase(pi)}
                    readOnly={readOnly}
                  />
                </SortablePhaseWrapper>
              ))}
            </SortableContext>
          </DndContext>

          {!readOnly && (
            <button
              type="button"
              onClick={addPhase}
              className="w-full flex items-center justify-center gap-1.5 py-3 text-sm text-slate-400 hover:text-indigo-600 hover:bg-indigo-50 border-2 border-dashed border-slate-200 hover:border-indigo-300 rounded-lg transition-colors"
            >
              <Plus size={15} /> Add Phase
            </button>
          )}
        </div>

        {/* JSON preview */}
        {showJson && <JsonPreview pipeline={value} validationErrors={validationErrors} />}
      </div>
    </div>
  );
};

// ─── Sortable Phase Wrapper ──────────────────────────────────────────────────

function SortablePhaseWrapper({ id, children, readOnly }: { id: string; children: React.ReactNode; readOnly?: boolean }) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id });
  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.5 : 1,
  };

  return (
    <div ref={setNodeRef} style={style}>
      {!readOnly && (
        <div {...attributes} {...listeners} className="flex justify-center py-1 cursor-grab touch-none">
          <GripVertical size={16} className="text-slate-300" />
        </div>
      )}
      {children}
    </div>
  );
}

// ─── Validation ──────────────────────────────────────────────────────────────

function validatePipeline(pipeline: PipelineDef): string[] {
  const errors: string[] = [];
  if (!pipeline.phases || pipeline.phases.length === 0) {
    errors.push('At least one phase is required.');
  }
  const phaseNames = new Set<string>();
  pipeline.phases.forEach((phase, pi) => {
    if (!phase.name) errors.push(`Phase ${pi + 1}: name is required.`);
    if (phaseNames.has(phase.name)) errors.push(`Phase "${phase.name}": duplicate name.`);
    phaseNames.add(phase.name);
    if (!phase.steps || phase.steps.length === 0) {
      errors.push(`Phase "${phase.name || pi + 1}": at least one step is required.`);
    }
    const stepNames = new Set<string>();
    phase.steps.forEach((step, si) => {
      if (!step.name) errors.push(`Phase "${phase.name}", Step ${si + 1}: name is required.`);
      if (stepNames.has(step.name)) errors.push(`Phase "${phase.name}", Step "${step.name}": duplicate name.`);
      stepNames.add(step.name);
      if (!step.action || !step.action.match(/^(builtin:|shell:).+/)) {
        errors.push(`Phase "${phase.name}", Step "${step.name || si + 1}": invalid action format.`);
      }
      if (step.timeout < 1) {
        errors.push(`Phase "${phase.name}", Step "${step.name || si + 1}": timeout must be >= 1.`);
      }
    });
  });
  return errors;
}

export default PipelineEditor;
