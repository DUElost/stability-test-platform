import { useEffect, useMemo, useRef, useState } from 'react';
import {
  ArrowDown,
  ArrowUp,
  Copy,
  Eye,
  EyeOff,
  GripVertical,
  Layers3,
  Pencil,
  Plus,
  RotateCcw,
  Trash2,
  X,
} from 'lucide-react';
import {
  DndContext,
  KeyboardSensor,
  PointerSensor,
  closestCenter,
  type DragEndEvent,
  useSensor,
  useSensors,
} from '@dnd-kit/core';
import {
  SortableContext,
  arrayMove,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import type { PipelineDef, PipelinePhase, PipelineStep } from '@/utils/api';
import { DynamicToolForm, type ParamSchema } from '@/components/task/DynamicToolForm';

interface ActionTemplateOption {
  id: number;
  name: string;
  action: string;
  version?: string | null;
  params: Record<string, any>;
  timeout_seconds: number;
  retry: number;
  is_active: boolean;
}

interface ScriptOption {
  id: number;
  name: string;
  version: string;
  category?: string | null;
  script_type: string;
  param_schema?: Record<string, any>;
  is_active: boolean;
}

interface StagesPipelineEditorProps {
  value: PipelineDef;
  onChange: (def: PipelineDef) => void;
  scriptOptions?: ScriptOption[];
  actionTemplateOptions?: ActionTemplateOption[];
  allowedPhases?: PipelinePhase[];
  readOnly?: boolean;
}

interface StepCardProps {
  id: string;
  step: PipelineStep;
  index: number;
  onEdit: () => void;
  onRemove: () => void;
  onDuplicate: () => void;
  onToggleEnabled: () => void;
  onMoveUp: () => void;
  onMoveDown: () => void;
  onInlineChange: (step: PipelineStep) => void;
  canMoveUp: boolean;
  canMoveDown: boolean;
  scriptOptions: ScriptOption[];
  readOnly?: boolean;
}

interface StepEditorDrawerProps {
  open: boolean;
  title: string;
  step: PipelineStep | null;
  onClose: () => void;
  onChange: (step: PipelineStep) => void;
  scriptOptions: ScriptOption[];
  actionTemplateOptions: ActionTemplateOption[];
  readOnly?: boolean;
}

const PHASES: {
  key: PipelinePhase;
  label: string;
  hint: string;
  color: string;
  chipClass: string;
}[] = [
  {
    key: 'init',
    label: 'Init',
    hint: '生命周期初始化步骤',
    color: 'border-slate-200 bg-slate-50',
    chipClass: 'bg-slate-100 text-slate-700',
  },
  {
    key: 'patrol',
    label: 'Patrol',
    hint: '周期性巡检步骤',
    color: 'border-emerald-200 bg-emerald-50',
    chipClass: 'bg-emerald-100 text-emerald-700',
  },
  {
    key: 'teardown',
    label: 'Teardown',
    hint: '生命周期收尾步骤',
    color: 'border-amber-200 bg-amber-50',
    chipClass: 'bg-amber-100 text-amber-700',
  },
];

function createEmptyStep(phaseName: string, index: number, scriptOptions: ScriptOption[]): PipelineStep {
  const firstScript = scriptOptions.find((script) => script.is_active) ?? scriptOptions[0];
  return {
    step_id: `${phaseName}_step_${index + 1}`,
    action: firstScript ? `script:${firstScript.name}` : 'script:',
    version: firstScript?.version ?? '',
    timeout_seconds: 300,
    retry: 0,
    params: {},
    enabled: true,
  };
}

function normalizeStepEnabled(step: PipelineStep): PipelineStep {
  return { ...step, enabled: step.enabled !== false };
}

function duplicateStep(step: PipelineStep, existing: PipelineStep[]): PipelineStep {
  const base = `${step.step_id}_copy`;
  let candidate = base;
  let suffix = 2;
  while (existing.some((item) => item.step_id === candidate)) {
    candidate = `${base}_${suffix}`;
    suffix += 1;
  }
  return {
    ...normalizeStepEnabled(step),
    step_id: candidate,
  };
}

function clampTimeout(value: string): number {
  return Math.max(1, Number.parseInt(value, 10) || 1);
}

function clampRetry(value: string): number {
  return Math.min(10, Math.max(0, Number.parseInt(value, 10) || 0));
}

function getActionMeta(
  step: PipelineStep,
  scriptOptions: ScriptOption[],
) {
  const scriptName = step.action.startsWith('script:') ? step.action.replace('script:', '') : '';
  const selectedScript = scriptOptions.find((x) => x.name === scriptName && x.version === step.version)
    ?? scriptOptions.find((x) => x.name === scriptName);

  return {
    scriptName,
    selectedScript,
  };
}

function getPhaseSteps(value: PipelineDef, phase: PipelinePhase): PipelineStep[] {
  if (phase === 'patrol') return value.lifecycle?.patrol?.steps ?? [];
  return value.lifecycle?.[phase] ?? [];
}

function setPhaseSteps(value: PipelineDef, phase: PipelinePhase, steps: PipelineStep[]): PipelineDef {
  const lifecycle = value.lifecycle ?? { init: [], teardown: [] };
  if (phase === 'patrol') {
    return {
      lifecycle: {
        ...lifecycle,
        init: lifecycle.init ?? [],
        teardown: lifecycle.teardown ?? [],
        patrol: {
          interval_seconds: lifecycle.patrol?.interval_seconds ?? 60,
          steps,
        },
      },
    };
  }
  return {
    lifecycle: {
      ...lifecycle,
      init: lifecycle.init ?? [],
      teardown: lifecycle.teardown ?? [],
      [phase]: steps,
    },
  };
}

function StepCard({
  id,
  step,
  index,
  onEdit,
  onRemove,
  onDuplicate,
  onToggleEnabled,
  onMoveUp,
  onMoveDown,
  onInlineChange,
  canMoveUp,
  canMoveDown,
  scriptOptions,
  readOnly,
}: StepCardProps) {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id, disabled: readOnly });
  const { scriptName, selectedScript } = getActionMeta(
    step,
    scriptOptions,
  );
  const paramsCount = Object.keys(step.params ?? {}).length;
  const enabled = step.enabled !== false;
  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
  };

  return (
    <div
      ref={setNodeRef}
      style={style}
      className={`mb-2 rounded-xl border px-3 py-2 shadow-sm ${
        enabled ? 'border-gray-200 bg-white' : 'border-dashed border-gray-300 bg-gray-50'
      } ${isDragging ? 'opacity-70 shadow-md' : ''}`}
    >
      <div className="flex items-start gap-3">
        <button
          type="button"
          className="mt-0.5 flex h-7 w-7 items-center justify-center rounded text-gray-400 hover:bg-gray-100 hover:text-gray-600 disabled:cursor-not-allowed disabled:opacity-40"
          title="拖拽排序"
          aria-label={`拖拽排序 Step ${step.step_id}`}
          disabled={readOnly}
          {...attributes}
          {...listeners}
        >
          <GripVertical className="h-4 w-4" />
        </button>
        <div className="mt-1 w-5 text-center text-xs font-mono text-gray-500">{index + 1}</div>
        <div className="min-w-0 flex-1">
          <div className="grid gap-2 md:grid-cols-[minmax(0,1fr)_120px_96px]">
            <input
              aria-label={`Step ID ${step.step_id}`}
              className="h-8 min-w-0 rounded-md border border-gray-200 bg-white px-2 text-sm font-medium text-gray-800 focus:border-slate-400 focus:outline-none disabled:bg-gray-100"
              value={step.step_id}
              disabled={readOnly}
              onChange={(event) => onInlineChange({ ...step, step_id: event.target.value })}
            />
            <input
              aria-label={`Timeout ${step.step_id}`}
              className="h-8 rounded-md border border-gray-200 bg-white px-2 text-xs text-gray-700 focus:border-slate-400 focus:outline-none disabled:bg-gray-100"
              type="number"
              min={1}
              value={step.timeout_seconds}
              disabled={readOnly}
              onChange={(event) => onInlineChange({ ...step, timeout_seconds: clampTimeout(event.target.value) })}
            />
            <input
              aria-label={`Retry ${step.step_id}`}
              className="h-8 rounded-md border border-gray-200 bg-white px-2 text-xs text-gray-700 focus:border-slate-400 focus:outline-none disabled:bg-gray-100"
              type="number"
              min={0}
              max={10}
              value={step.retry ?? 0}
              disabled={readOnly}
              onChange={(event) => onInlineChange({ ...step, retry: clampRetry(event.target.value) })}
            />
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[11px] text-gray-500">
            <span className="rounded bg-slate-100 px-1.5 py-0.5 text-slate-700">
              {step.action.startsWith('script:')
                ? `script:${selectedScript?.name || scriptName}`
                : step.action || 'script:'}
            </span>
            <span className="rounded bg-gray-100 px-1.5 py-0.5">params {paramsCount}</span>
            {!enabled && <span className="rounded bg-amber-100 px-1.5 py-0.5 text-amber-700">disabled</span>}
          </div>
        </div>

        <div className="flex items-center gap-1">
          {!readOnly && (
            <>
              <button
                type="button"
                className="flex h-7 w-7 items-center justify-center rounded text-gray-400 hover:bg-gray-100 hover:text-gray-600 disabled:cursor-not-allowed disabled:opacity-40"
                onClick={onMoveUp}
                disabled={!canMoveUp}
                title="上移 Step"
                aria-label={`上移 Step ${step.step_id}`}
              >
                <ArrowUp className="h-4 w-4" />
              </button>
              <button
                type="button"
                className="flex h-7 w-7 items-center justify-center rounded text-gray-400 hover:bg-gray-100 hover:text-gray-600 disabled:cursor-not-allowed disabled:opacity-40"
                onClick={onMoveDown}
                disabled={!canMoveDown}
                title="下移 Step"
                aria-label={`下移 Step ${step.step_id}`}
              >
                <ArrowDown className="h-4 w-4" />
              </button>
              <button
                type="button"
                className="flex h-7 w-7 items-center justify-center rounded text-gray-400 hover:bg-gray-100 hover:text-gray-600"
                onClick={onDuplicate}
                title="复制 Step"
                aria-label={`复制 Step ${step.step_id}`}
              >
                <Copy className="h-4 w-4" />
              </button>
              <button
                type="button"
                className="flex h-7 w-7 items-center justify-center rounded text-gray-400 hover:bg-gray-100 hover:text-gray-600"
                onClick={onToggleEnabled}
                title={enabled ? '禁用 Step' : '启用 Step'}
                aria-label={`${enabled ? '禁用' : '启用'} Step ${step.step_id}`}
              >
                {enabled ? <Eye className="h-4 w-4" /> : <EyeOff className="h-4 w-4" />}
              </button>
            </>
          )}
          <button
            type="button"
            className="flex h-7 w-7 items-center justify-center rounded text-gray-400 hover:bg-gray-100 hover:text-gray-600"
            onClick={onEdit}
            title="编辑 Step"
            aria-label={`编辑 Step ${step.step_id}`}
          >
            <Pencil className="h-4 w-4" />
          </button>
          {!readOnly && (
            <button
              type="button"
              className="flex h-7 w-7 items-center justify-center rounded text-gray-300 hover:bg-red-50 hover:text-red-500"
              onClick={onRemove}
              title="删除 Step"
              aria-label={`删除 Step ${step.step_id}`}
            >
              <Trash2 className="h-4 w-4" />
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

function StepEditorDrawer({
  open,
  title,
  step,
  onClose,
  onChange,
  scriptOptions,
  actionTemplateOptions,
  readOnly,
}: StepEditorDrawerProps) {
  const [paramsText, setParamsText] = useState('');
  const [paramsError, setParamsError] = useState('');
  const [formValues, setFormValues] = useState<Record<string, any>>({});
  const [validationErrors, setValidationErrors] = useState<Record<string, string>>({});
  const [touchedFields, setTouchedFields] = useState<Set<string>>(new Set());
  const initKeyRef = useRef('');

  const meta = useMemo(() => {
    if (!step) {
      return {
        scriptName: '',
        selectedScript: undefined,
      };
    }
    return getActionMeta(step, scriptOptions);
  }, [step, scriptOptions]);

  const paramSchema = useMemo<ParamSchema | null>(() => {
    const schema = meta.selectedScript?.param_schema;
    return schema && Object.keys(schema).length > 0 ? (schema as ParamSchema) : null;
  }, [meta]);

  // Initialize form/textarea when step identity or action changes
  useEffect(() => {
    if (!step) {
      setParamsText('');
      setParamsError('');
      setFormValues({});
      setValidationErrors({});
      setTouchedFields(new Set());
      initKeyRef.current = '';
      return;
    }

    const initKey = `${step.step_id}::${step.action}::${paramSchema ? 'form' : 'text'}`;
    if (initKey === initKeyRef.current) return;
    initKeyRef.current = initKey;

    if (paramSchema) {
      const merged: Record<string, any> = {};
      for (const [key, field] of Object.entries(paramSchema)) {
        if (step.params && key in step.params) {
          merged[key] = step.params[key];
        } else if (field.default !== undefined) {
          merged[key] = field.default;
        }
      }
      setFormValues(merged);
      setParamsText(Object.keys(merged).length > 0 ? JSON.stringify(merged, null, 2) : '');
    } else {
      setParamsText(
        step.params && Object.keys(step.params).length > 0
          ? JSON.stringify(step.params, null, 2)
          : '',
      );
    }
    setParamsError('');
    setValidationErrors({});
    setTouchedFields(new Set());
  }, [step, paramSchema]);

  const selectedTemplateId = useMemo(() => {
    if (!step) return '';
    return actionTemplateOptions.find((tpl) => (
      tpl.action === step.action
      && (tpl.version ?? undefined) === (step.version ?? undefined)
      && tpl.timeout_seconds === step.timeout_seconds
      && (tpl.retry ?? 0) === (step.retry ?? 0)
      && JSON.stringify(tpl.params || {}) === JSON.stringify(step.params || {})
    ))?.id ?? '';
  }, [actionTemplateOptions, step]);

  const scriptGroups = useMemo(() => {
    const grouped: Record<string, ScriptOption[]> = {};
    scriptOptions.filter((x) => x.is_active).forEach((item) => {
      const category = item.category || 'script';
      grouped[category] = grouped[category] || [];
      grouped[category].push(item);
    });
    Object.values(grouped).forEach((items) => items.sort((a, b) => a.name.localeCompare(b.name)));
    return grouped;
  }, [scriptOptions]);

  const handleScriptChange = (name: string) => {
    if (!step) return;
    const script = scriptOptions.find((x) => x.name === name);
    if (!script) return;
    onChange({ ...step, action: `script:${script.name}`, version: script.version });
  };

  const handleParamsBlur = () => {
    if (!step) return;

    if (!paramsText.trim()) {
      onChange({ ...step, params: {} });
      setParamsError('');
      return;
    }

    try {
      const parsed = JSON.parse(paramsText);
      onChange({ ...step, params: parsed });
      setParamsError('');
    } catch {
      setParamsError('JSON 格式错误');
    }
  };

  const handleFormFieldChange = (key: string, value: any) => {
    if (!step || !paramSchema) return;
    const next = { ...formValues, [key]: value };
    setFormValues(next);
    setTouchedFields((prev) => new Set(prev).add(key));

    // Validate ALL required fields against the updated values
    const errors: Record<string, string> = {};
    for (const [k, field] of Object.entries(paramSchema)) {
      if (field.required && (next[k] === undefined || next[k] === '')) {
        errors[k] = `${field.label || k} 不能为空`;
      }
    }
    setValidationErrors(errors);

    // Only sync to parent when all required fields are valid
    if (Object.keys(errors).length === 0) {
      onChange({ ...step, params: next });
    }
  };

  const handleApplyTemplate = (templateIdText: string) => {
    if (!step || !templateIdText) return;

    const templateId = parseInt(templateIdText, 10);
    if (Number.isNaN(templateId)) return;

    const template = actionTemplateOptions.find((x) => x.id === templateId);
    if (!template) return;

    onChange({
      ...step,
      action: template.action,
      version: template.version ?? undefined,
      params: template.params || {},
      timeout_seconds: template.timeout_seconds,
      retry: template.retry ?? 0,
    });
    setParamsText(
      template.params && Object.keys(template.params).length > 0
        ? JSON.stringify(template.params, null, 2)
        : '',
    );
    setParamsError('');
    // Reset init key so useEffect re-initializes for new template
    initKeyRef.current = '';
  };

  // Visible validation errors: only show for fields the user has touched
  const visibleErrors = useMemo(() => {
    const out: Record<string, string> = {};
    for (const [key, msg] of Object.entries(validationErrors)) {
      if (touchedFields.has(key)) {
        out[key] = msg;
      }
    }
    return out;
  }, [validationErrors, touchedFields]);

  if (!open || !step) return null;

  return (
    <>
      <div className="fixed inset-0 z-40 bg-black/30" onClick={onClose} />
      <aside className="fixed right-0 top-0 z-50 h-full w-full max-w-2xl border-l border-gray-200 bg-white shadow-xl">
        <div className="flex h-full flex-col">
          <div className="flex items-center justify-between border-b px-4 py-3">
            <div>
              <h3 className="text-sm font-semibold text-gray-900">{title}</h3>
              <p className="text-xs text-gray-500">{step.step_id}</p>
            </div>
            <button
              type="button"
              className="rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-600"
              onClick={onClose}
            >
              <X className="h-4 w-4" />
            </button>
          </div>

          <div className="flex-1 space-y-4 overflow-y-auto px-4 py-4">
            <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
              <div>
                <label className="mb-1 block text-xs font-medium text-gray-600">step_id</label>
                <input
                  type="text"
                  value={step.step_id}
                  onChange={(e) => onChange({ ...step, step_id: e.target.value })}
                  disabled={readOnly}
                  className="w-full rounded border px-2 py-1.5 font-mono text-sm focus:outline-none focus:ring-2 focus:ring-slate-500/20"
                />
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium text-gray-600">timeout_seconds</label>
                <input
                  type="number"
                  min={1}
                  value={step.timeout_seconds}
                  onChange={(e) => onChange({ ...step, timeout_seconds: parseInt(e.target.value, 10) || 300 })}
                  disabled={readOnly}
                  className="w-full rounded border px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-slate-500/20"
                />
              </div>
            </div>

            <div>
              <label className="mb-1 block text-xs font-medium text-gray-600">action</label>
              <select
                value={meta.scriptName}
                onChange={(e) => handleScriptChange(e.target.value)}
                disabled={readOnly || scriptOptions.length === 0}
                className="w-full rounded border bg-white px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-slate-500/20"
              >
                {scriptOptions.length === 0 && <option value="">无可用 script</option>}
                {Object.entries(scriptGroups).map(([category, items]) => (
                  <optgroup key={category} label={category}>
                    {items.map((script) => (
                      <option key={`${script.name}:${script.version}`} value={script.name}>
                        {script.name} v{script.version}
                      </option>
                    ))}
                  </optgroup>
                ))}
              </select>
              {step.version && (
                <p className="mt-1 text-xs text-gray-500">锁定版本: v{step.version}</p>
              )}
            </div>

            {actionTemplateOptions.length > 0 && (
              <div>
                <label className="mb-1 block text-xs font-medium text-gray-600">应用模板</label>
                <select
                  value={selectedTemplateId}
                  onChange={(e) => handleApplyTemplate(e.target.value)}
                  disabled={readOnly}
                  className="w-full rounded border bg-white px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-slate-500/20"
                >
                  <option value="">选择模板并填充当前 Step</option>
                  {actionTemplateOptions.filter((x) => x.is_active && x.action.startsWith('script:')).map((item) => (
                    <option key={item.id} value={item.id}>{item.name}</option>
                  ))}
                </select>
              </div>
            )}

            <div>
              <label className="mb-1 block text-xs font-medium text-gray-600">retry</label>
              <input
                type="number"
                min={0}
                max={5}
                value={step.retry ?? 0}
                onChange={(e) => onChange({ ...step, retry: parseInt(e.target.value, 10) || 0 })}
                disabled={readOnly}
                className="w-24 rounded border px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-slate-500/20"
              />
            </div>

            <div>
              <label className="mb-1 block text-xs font-medium text-gray-600">
                params{paramSchema ? '' : ' (JSON)'}
              </label>
              {paramSchema ? (
                <div className="rounded border border-gray-200 bg-gray-50/50 p-3">
                  <DynamicToolForm
                    schema={paramSchema}
                    values={formValues}
                    onChange={handleFormFieldChange}
                    errors={visibleErrors}
                  />
                </div>
              ) : (
                <>
                  <textarea
                    value={paramsText}
                    onChange={(e) => setParamsText(e.target.value)}
                    onBlur={handleParamsBlur}
                    disabled={readOnly}
                    rows={12}
                    placeholder="{}"
                    className="w-full resize-y rounded border px-2 py-1.5 font-mono text-sm focus:outline-none focus:ring-2 focus:ring-slate-500/20"
                  />
                  {paramsError && <p className="mt-1 text-xs text-red-500">{paramsError}</p>}
                </>
              )}
            </div>
          </div>
        </div>
      </aside>
    </>
  );
}

export default function StagesPipelineEditor({
  value,
  onChange,
  scriptOptions = [],
  actionTemplateOptions = [],
  allowedPhases,
  readOnly = false,
}: StagesPipelineEditorProps) {
  const [viewMode, setViewMode] = useState<'focus' | 'all'>('focus');
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );
  const phaseKeys = useMemo(
    () => new Set<PipelinePhase>(allowedPhases ?? ['init', 'patrol', 'teardown']),
    [allowedPhases],
  );
  const visiblePhaseDefs = useMemo(() => PHASES.filter((phase) => phaseKeys.has(phase.key)), [phaseKeys]);
  const [activePhase, setActivePhase] = useState<PipelinePhase>(allowedPhases?.[0] ?? 'init');
  const [editingStep, setEditingStep] = useState<{ phase: PipelinePhase; index: number } | null>(null);

  useEffect(() => {
    if (!phaseKeys.has(activePhase)) {
      setActivePhase(visiblePhaseDefs[0]?.key ?? 'init');
    }
  }, [activePhase, phaseKeys, visiblePhaseDefs]);

  const phaseMeta = useMemo(() => {
    const map: Record<PipelinePhase, { label: string; hint: string }> = {
      init: { label: 'Init', hint: '生命周期初始化步骤' },
      patrol: { label: 'Patrol', hint: '周期性巡检步骤' },
      teardown: { label: 'Teardown', hint: '生命周期收尾步骤' },
    };
    return map;
  }, []);

  const updatePhase = (key: PipelinePhase, steps: PipelineStep[]) => {
    onChange(setPhaseSteps(value, key, steps));
  };

  const addStep = (key: PipelinePhase) => {
    const current = getPhaseSteps(value, key);
    updatePhase(key, [...current, createEmptyStep(key, current.length, scriptOptions)]);
    setActivePhase(key);
    setEditingStep({ phase: key, index: current.length });
  };

  const updateStep = (key: PipelinePhase, index: number, step: PipelineStep) => {
    const current = [...getPhaseSteps(value, key)];
    current[index] = step;
    updatePhase(key, current);
  };

  const removeStep = (key: PipelinePhase, index: number) => {
    const current = [...getPhaseSteps(value, key)];
    current.splice(index, 1);
    updatePhase(key, current);

    setEditingStep((prev) => {
      if (!prev || prev.phase !== key) return prev;
      if (prev.index === index) return null;
      if (prev.index > index) return { ...prev, index: prev.index - 1 };
      return prev;
    });
  };

  const duplicateStepAt = (key: PipelinePhase, index: number) => {
    const current = [...getPhaseSteps(value, key)];
    const source = current[index];
    if (!source) return;
    current.splice(index + 1, 0, duplicateStep(source, current));
    updatePhase(key, current);
  };

  const toggleStepEnabled = (key: PipelinePhase, index: number) => {
    const current = [...getPhaseSteps(value, key)];
    const step = current[index];
    if (!step) return;
    current[index] = { ...step, enabled: step.enabled === false };
    updatePhase(key, current);
  };

  const moveStep = (key: PipelinePhase, index: number, direction: -1 | 1) => {
    const current = [...getPhaseSteps(value, key)];
    const target = index + direction;
    if (target < 0 || target >= current.length) return;
    updatePhase(key, arrayMove(current, index, target));
    setEditingStep((prev) => {
      if (!prev || prev.phase !== key) return prev;
      if (prev.index === index) return { ...prev, index: target };
      if (prev.index === target) return { ...prev, index };
      return prev;
    });
  };

  const handleDragEnd = (
    key: PipelinePhase,
    itemIds: string[],
    event: DragEndEvent,
  ) => {
    const { active, over } = event;
    if (!over || active.id === over.id) return;
    const oldIndex = itemIds.indexOf(String(active.id));
    const newIndex = itemIds.indexOf(String(over.id));
    if (oldIndex < 0 || newIndex < 0) return;
    updatePhase(key, arrayMove(getPhaseSteps(value, key), oldIndex, newIndex));
    setEditingStep((prev) => {
      if (!prev || prev.phase !== key) return prev;
      if (prev.index === oldIndex) return { ...prev, index: newIndex };
      return prev;
    });
  };

  const visiblePhases = viewMode === 'focus'
    ? visiblePhaseDefs.filter((phase) => phase.key === activePhase)
    : visiblePhaseDefs;

  const editingStepData = editingStep
    ? getPhaseSteps(value, editingStep.phase)[editingStep.index] || null
    : null;

  return (
    <div className="space-y-4">
      <div className="rounded-lg border border-gray-200 bg-white p-3">
        <div className="flex flex-col gap-3 xl:flex-row xl:items-center xl:justify-between">
          <div className="grid gap-2 sm:grid-cols-3">
            {visiblePhaseDefs.map((phase) => {
              const count = getPhaseSteps(value, phase.key).length;
              const isActive = phase.key === activePhase;
              return (
                <button
                  key={phase.key}
                  type="button"
                  onClick={() => {
                    setActivePhase(phase.key);
                    setViewMode('focus');
                  }}
                  className={`rounded-lg border px-3 py-2 text-left transition-colors ${
                    isActive
                      ? 'border-slate-300 bg-slate-50'
                      : 'border-gray-200 bg-white hover:border-gray-300 hover:bg-gray-50'
                  }`}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-sm font-semibold text-gray-800">{phase.label}</span>
                    <span className="rounded-full bg-gray-100 px-2 py-0.5 text-[11px] text-gray-600">
                      {count} Step
                    </span>
                  </div>
                  <p className="mt-1 truncate text-xs text-gray-500">{phase.hint}</p>
                </button>
              );
            })}
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              className={`rounded-md border px-3 py-1.5 text-xs transition-colors ${
                viewMode === 'focus'
                  ? 'border-slate-300 bg-slate-100 text-slate-700'
                  : 'border-gray-200 bg-white text-gray-600 hover:bg-gray-50'
              }`}
              onClick={() => setViewMode('focus')}
            >
              聚焦单阶段
            </button>
            <button
              type="button"
              className={`rounded-md border px-3 py-1.5 text-xs transition-colors ${
                viewMode === 'all'
                  ? 'border-slate-300 bg-slate-100 text-slate-700'
                  : 'border-gray-200 bg-white text-gray-600 hover:bg-gray-50'
              }`}
              onClick={() => setViewMode('all')}
            >
              多阶段并排
            </button>
          </div>
        </div>
      </div>

      <div className={viewMode === 'all' ? 'grid gap-4 xl:grid-cols-2 2xl:grid-cols-3' : 'grid gap-4 grid-cols-1'}>
        {visiblePhases.map(({ key, label, hint, color, chipClass }) => {
          const steps = getPhaseSteps(value, key);
          const itemIds = steps.map((step, index) => `${key}-${step.step_id || 'step'}-${index}`);

          return (
            <section key={key} className={`rounded-xl border-2 p-3 ${color}`}>
              <div className="mb-3 flex items-start justify-between gap-2">
                <div>
                  <h3 className="text-sm font-semibold text-gray-800">{label}</h3>
                  <p className="mt-0.5 text-xs text-gray-500">{hint}</p>
                </div>
                <div className="flex items-center gap-2">
                  <span className={`rounded-full px-2 py-0.5 text-[11px] ${chipClass}`}>{steps.length} Step</span>
                  {!readOnly && (
                    <button
                      type="button"
                      className="rounded p-1 text-gray-500 transition-colors hover:bg-white/80 hover:text-gray-700"
                      onClick={() => addStep(key)}
                      title="添加 Step"
                    >
                      <Plus className="h-4 w-4" />
                    </button>
                  )}
                </div>
              </div>

              {steps.length === 0 ? (
                <button
                  type="button"
                  className="w-full rounded-lg border-2 border-dashed border-gray-300 bg-white/60 py-8 text-center text-xs text-gray-500 transition-colors hover:bg-white"
                  onClick={() => {
                    if (!readOnly) addStep(key);
                  }}
                  disabled={readOnly}
                >
                  <div className="mb-1 flex items-center justify-center gap-1 text-gray-400">
                    <Layers3 className="h-3.5 w-3.5" />
                    空阶段
                  </div>
                  <div>点击添加第一个 Step</div>
                </button>
              ) : (
                <DndContext
                  sensors={sensors}
                  collisionDetection={closestCenter}
                  onDragEnd={(event) => handleDragEnd(key, itemIds, event)}
                >
                  <SortableContext items={itemIds} strategy={verticalListSortingStrategy}>
                    <div>
                      {steps.map((step, index) => (
                        <StepCard
                          id={itemIds[index]}
                          key={itemIds[index]}
                          step={step}
                          index={index}
                          onEdit={() => setEditingStep({ phase: key, index })}
                          onRemove={() => removeStep(key, index)}
                          onDuplicate={() => duplicateStepAt(key, index)}
                          onToggleEnabled={() => toggleStepEnabled(key, index)}
                          onMoveUp={() => moveStep(key, index, -1)}
                          onMoveDown={() => moveStep(key, index, 1)}
                          onInlineChange={(next) => updateStep(key, index, next)}
                          canMoveUp={index > 0}
                          canMoveDown={index < steps.length - 1}
                          scriptOptions={scriptOptions}
                          readOnly={readOnly}
                        />
                      ))}
                    </div>
                  </SortableContext>
                </DndContext>
              )}

              {steps.length > 0 && !readOnly && (
                <button
                  type="button"
                  className="mt-1 flex w-full items-center justify-center gap-1 rounded-lg border border-dashed border-gray-300 bg-white/60 py-2 text-xs text-gray-600 hover:bg-white"
                  onClick={() => addStep(key)}
                >
                  <Plus className="h-3.5 w-3.5" />
                  新增 Step
                </button>
              )}

              {steps.length > 0 && readOnly && (
                <div className="mt-2 flex items-center justify-center gap-1 text-xs text-gray-400">
                  <RotateCcw className="h-3.5 w-3.5" />
                  只读模式
                </div>
              )}
            </section>
          );
        })}
      </div>

      <StepEditorDrawer
        open={!!editingStepData}
        title={editingStep ? `${phaseMeta[editingStep.phase].label} · Step ${editingStep.index + 1}` : ''}
        step={editingStepData}
        onClose={() => setEditingStep(null)}
        onChange={(next) => {
          if (!editingStep) return;
          updateStep(editingStep.phase, editingStep.index, next);
        }}
        scriptOptions={scriptOptions}
        actionTemplateOptions={actionTemplateOptions}
        readOnly={readOnly}
      />
    </div>
  );
}
