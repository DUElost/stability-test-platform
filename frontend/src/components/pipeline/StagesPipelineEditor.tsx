import { useEffect, useMemo, useState } from 'react';
import { Plus, Trash2, Layers3, RotateCcw, Pencil, X } from 'lucide-react';
import type { PipelineDef, PipelineStep } from '@/utils/api';
import { BUILTIN_ACTIONS } from './actionCatalog';

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

interface BuiltinActionOption {
  name: string;
  label: string;
  category: 'device' | 'process' | 'file' | 'log' | 'script';
  description: string;
  param_schema: Record<string, any>;
  is_active: boolean;
}

interface StagesPipelineEditorProps {
  value: PipelineDef;
  onChange: (def: PipelineDef) => void;
  toolOptions?: { id: number; name: string; version: string }[];
  builtinOptions?: BuiltinActionOption[];
  actionTemplateOptions?: ActionTemplateOption[];
  readOnly?: boolean;
}

interface StepCardProps {
  step: PipelineStep;
  index: number;
  onEdit: () => void;
  onRemove: () => void;
  toolOptions: { id: number; name: string; version: string }[];
  builtinOptions: BuiltinActionOption[];
  readOnly?: boolean;
}

interface StepEditorDrawerProps {
  open: boolean;
  title: string;
  step: PipelineStep | null;
  onClose: () => void;
  onChange: (step: PipelineStep) => void;
  toolOptions: { id: number; name: string; version: string }[];
  builtinOptions: BuiltinActionOption[];
  actionTemplateOptions: ActionTemplateOption[];
  readOnly?: boolean;
}

const STAGES: {
  key: keyof PipelineDef['stages'];
  label: string;
  hint: string;
  color: string;
  chipClass: string;
}[] = [
  {
    key: 'prepare',
    label: 'Prepare',
    hint: '环境校验与前置准备',
    color: 'border-slate-200 bg-slate-50',
    chipClass: 'bg-slate-100 text-slate-700',
  },
  {
    key: 'execute',
    label: 'Execute',
    hint: '核心测试动作执行',
    color: 'border-emerald-200 bg-emerald-50',
    chipClass: 'bg-emerald-100 text-emerald-700',
  },
  {
    key: 'post_process',
    label: 'Post Process',
    hint: '收尾与结果产物处理',
    color: 'border-amber-200 bg-amber-50',
    chipClass: 'bg-amber-100 text-amber-700',
  },
];

const BUILTIN_CATEGORY_LABEL: Record<BuiltinActionOption['category'], string> = {
  device: '设备',
  process: '进程',
  file: '文件',
  log: '日志',
  script: '脚本',
};

function createEmptyStep(stageName: string, index: number, defaultBuiltin = 'check_device'): PipelineStep {
  return {
    step_id: `${stageName}_step_${index + 1}`,
    action: `builtin:${defaultBuiltin}`,
    timeout_seconds: 300,
    retry: 0,
    params: {},
  };
}

function getActionMeta(
  step: PipelineStep,
  toolOptions: { id: number; name: string; version: string }[],
  builtinOptions: BuiltinActionOption[],
) {
  const actionType = step.action.startsWith('tool:') ? 'tool' : 'builtin';
  const builtinName = actionType === 'builtin' ? step.action.replace('builtin:', '') : '';
  const toolId = actionType === 'tool' ? parseInt(step.action.replace('tool:', ''), 10) : 0;
  const selectedBuiltin = builtinOptions.find((x) => x.name === builtinName);
  const selectedTool = toolOptions.find((x) => x.id === toolId);

  return {
    actionType,
    builtinName,
    toolId,
    selectedBuiltin,
    selectedTool,
  };
}

function StepCard({
  step,
  index,
  onEdit,
  onRemove,
  toolOptions,
  builtinOptions,
  readOnly,
}: StepCardProps) {
  const { actionType, builtinName, toolId, selectedBuiltin, selectedTool } = getActionMeta(step, toolOptions, builtinOptions);
  const paramsCount = Object.keys(step.params ?? {}).length;

  return (
    <div className="mb-2 rounded-xl border border-gray-200 bg-white px-3 py-2 shadow-sm">
      <div className="flex items-start gap-3">
        <div className="mt-0.5 w-5 text-center text-xs font-mono text-gray-500">{index + 1}</div>
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium text-gray-800">{step.step_id}</p>
          <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[11px] text-gray-500">
            <span className="rounded bg-slate-100 px-1.5 py-0.5 text-slate-700">
              {actionType === 'tool'
                ? `tool:${selectedTool?.name || toolId}`
                : `builtin:${selectedBuiltin?.name || builtinName}`}
            </span>
            <span className="rounded bg-gray-100 px-1.5 py-0.5">timeout {step.timeout_seconds}s</span>
            <span className="rounded bg-gray-100 px-1.5 py-0.5">retry {step.retry ?? 0}</span>
            <span className="rounded bg-gray-100 px-1.5 py-0.5">params {paramsCount}</span>
          </div>
        </div>

        <div className="flex items-center gap-1">
          <button
            type="button"
            className="rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-600"
            onClick={onEdit}
            title="编辑 Step"
          >
            <Pencil className="h-4 w-4" />
          </button>
          {!readOnly && (
            <button
              type="button"
              className="rounded p-1 text-gray-300 hover:bg-red-50 hover:text-red-500"
              onClick={onRemove}
              title="删除 Step"
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
  toolOptions,
  builtinOptions,
  actionTemplateOptions,
  readOnly,
}: StepEditorDrawerProps) {
  const [paramsText, setParamsText] = useState('');
  const [paramsError, setParamsError] = useState('');

  useEffect(() => {
    if (!step) {
      setParamsText('');
      setParamsError('');
      return;
    }
    setParamsText(
      step.params && Object.keys(step.params).length > 0
        ? JSON.stringify(step.params, null, 2)
        : '',
    );
    setParamsError('');
  }, [step]);

  const meta = useMemo(() => {
    if (!step) {
      return {
        actionType: 'builtin' as 'builtin' | 'tool',
        builtinName: '',
        toolId: 0,
        selectedBuiltin: undefined,
        selectedTool: undefined,
      };
    }
    return getActionMeta(step, toolOptions, builtinOptions);
  }, [step, toolOptions, builtinOptions]);

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

  const builtinGroups = useMemo(() => {
    const grouped: Record<BuiltinActionOption['category'], BuiltinActionOption[]> = {
      device: [],
      process: [],
      file: [],
      log: [],
      script: [],
    };

    builtinOptions.forEach((item) => {
      grouped[item.category].push(item);
    });

    (Object.keys(grouped) as BuiltinActionOption['category'][]).forEach((key) => {
      grouped[key].sort((a, b) => a.label.localeCompare(b.label));
    });

    return grouped;
  }, [builtinOptions]);

  const handleActionTypeChange = (type: 'builtin' | 'tool') => {
    if (!step) return;

    if (type === 'builtin') {
      const firstBuiltin = builtinOptions[0]?.name || 'check_device';
      onChange({ ...step, action: `builtin:${firstBuiltin}`, version: undefined });
      return;
    }

    if (toolOptions.length > 0) {
      const firstTool = toolOptions[0];
      onChange({ ...step, action: `tool:${firstTool.id}`, version: firstTool.version });
    }
  };

  const handleBuiltinChange = (name: string) => {
    if (!step) return;
    onChange({ ...step, action: `builtin:${name}`, version: undefined });
  };

  const handleToolChange = (idText: string) => {
    if (!step) return;
    const id = parseInt(idText, 10);
    const tool = toolOptions.find((x) => x.id === id);
    if (!tool) return;
    onChange({ ...step, action: `tool:${tool.id}`, version: tool.version });
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
  };

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
              <div className="flex flex-col gap-2 md:flex-row">
                <select
                  value={meta.actionType}
                  onChange={(e) => handleActionTypeChange(e.target.value as 'builtin' | 'tool')}
                  disabled={readOnly}
                  className="rounded border bg-white px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-slate-500/20"
                >
                  <option value="builtin">builtin:</option>
                  {toolOptions.length > 0 && <option value="tool">tool:</option>}
                </select>

                {meta.actionType === 'builtin' ? (
                  <select
                    value={meta.builtinName}
                    onChange={(e) => handleBuiltinChange(e.target.value)}
                    disabled={readOnly}
                    className="flex-1 rounded border bg-white px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-slate-500/20"
                  >
                    {(Object.keys(builtinGroups) as BuiltinActionOption['category'][]).map((category) => {
                      const items = builtinGroups[category];
                      if (items.length === 0) return null;
                      return (
                        <optgroup key={category} label={BUILTIN_CATEGORY_LABEL[category]}>
                          {items.map((item) => (
                            <option key={item.name} value={item.name}>
                              {item.label} ({item.name})
                            </option>
                          ))}
                        </optgroup>
                      );
                    })}
                  </select>
                ) : (
                  <select
                    value={meta.toolId}
                    onChange={(e) => handleToolChange(e.target.value)}
                    disabled={readOnly}
                    className="flex-1 rounded border bg-white px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-slate-500/20"
                  >
                    {toolOptions.map((tool) => (
                      <option key={tool.id} value={tool.id}>{tool.name} v{tool.version}</option>
                    ))}
                  </select>
                )}
              </div>
              {meta.actionType === 'builtin' && meta.selectedBuiltin?.description && (
                <p className="mt-1 text-xs text-gray-500">{meta.selectedBuiltin.description}</p>
              )}
              {meta.actionType === 'tool' && step.version && (
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
                  {actionTemplateOptions.filter((x) => x.is_active).map((item) => (
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
              <label className="mb-1 block text-xs font-medium text-gray-600">params (JSON)</label>
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
  toolOptions = [],
  builtinOptions = [],
  actionTemplateOptions = [],
  readOnly = false,
}: StagesPipelineEditorProps) {
  const stages = value.stages ?? {};
  const [viewMode, setViewMode] = useState<'focus' | 'all'>('focus');
  const [activeStage, setActiveStage] = useState<keyof PipelineDef['stages']>('prepare');
  const [editingStep, setEditingStep] = useState<{ stage: keyof PipelineDef['stages']; index: number } | null>(null);

  const stageMeta = useMemo(() => {
    const map: Record<keyof PipelineDef['stages'], { label: string; hint: string }> = {
      prepare: { label: 'Prepare', hint: '环境校验与前置准备' },
      execute: { label: 'Execute', hint: '核心测试动作执行' },
      post_process: { label: 'Post Process', hint: '收尾与结果产物处理' },
    };
    return map;
  }, []);

  const builtinCatalog = (builtinOptions.length > 0
    ? builtinOptions
    : BUILTIN_ACTIONS.map((x) => ({
      name: x.name,
      label: x.label,
      category: x.category,
      description: x.description,
      param_schema: x.paramSchema,
      is_active: true,
    }))
  ).filter((x) => x.is_active);

  const defaultBuiltin = builtinCatalog[0]?.name || 'check_device';

  const updateStage = (key: keyof PipelineDef['stages'], steps: PipelineStep[]) => {
    onChange({
      stages: {
        prepare: stages.prepare ?? [],
        execute: stages.execute ?? [],
        post_process: stages.post_process ?? [],
        [key]: steps,
      },
    });
  };

  const addStep = (key: keyof PipelineDef['stages']) => {
    const current = stages[key] || [];
    updateStage(key, [...current, createEmptyStep(key, current.length, defaultBuiltin)]);
    setActiveStage(key);
    setEditingStep({ stage: key, index: current.length });
  };

  const updateStep = (key: keyof PipelineDef['stages'], index: number, step: PipelineStep) => {
    const current = [...(stages[key] || [])];
    current[index] = step;
    updateStage(key, current);
  };

  const removeStep = (key: keyof PipelineDef['stages'], index: number) => {
    const current = [...(stages[key] || [])];
    current.splice(index, 1);
    updateStage(key, current);

    setEditingStep((prev) => {
      if (!prev || prev.stage !== key) return prev;
      if (prev.index === index) return null;
      if (prev.index > index) return { ...prev, index: prev.index - 1 };
      return prev;
    });
  };

  const visibleStages = viewMode === 'focus'
    ? STAGES.filter((stage) => stage.key === activeStage)
    : STAGES;

  const editingStepData = editingStep
    ? (stages[editingStep.stage] || [])[editingStep.index] || null
    : null;

  return (
    <div className="space-y-4">
      <div className="rounded-lg border border-gray-200 bg-white p-3">
        <div className="flex flex-col gap-3 xl:flex-row xl:items-center xl:justify-between">
          <div className="grid gap-2 sm:grid-cols-3">
            {STAGES.map((stage) => {
              const count = (stages[stage.key] || []).length;
              const isActive = stage.key === activeStage;
              return (
                <button
                  key={stage.key}
                  type="button"
                  onClick={() => {
                    setActiveStage(stage.key);
                    setViewMode('focus');
                  }}
                  className={`rounded-lg border px-3 py-2 text-left transition-colors ${
                    isActive
                      ? 'border-slate-300 bg-slate-50'
                      : 'border-gray-200 bg-white hover:border-gray-300 hover:bg-gray-50'
                  }`}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-sm font-semibold text-gray-800">{stage.label}</span>
                    <span className="rounded-full bg-gray-100 px-2 py-0.5 text-[11px] text-gray-600">
                      {count} Step
                    </span>
                  </div>
                  <p className="mt-1 truncate text-xs text-gray-500">{stage.hint}</p>
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
        {visibleStages.map(({ key, label, hint, color, chipClass }) => {
          const steps = stages[key] || [];

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
                <div>
                  {steps.map((step, index) => (
                    <StepCard
                      key={`${key}-${index}`}
                      step={step}
                      index={index}
                      onEdit={() => setEditingStep({ stage: key, index })}
                      onRemove={() => removeStep(key, index)}
                      toolOptions={toolOptions}
                      builtinOptions={builtinCatalog}
                      readOnly={readOnly}
                    />
                  ))}
                </div>
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
        title={editingStep ? `${stageMeta[editingStep.stage].label} · Step ${editingStep.index + 1}` : ''}
        step={editingStepData}
        onClose={() => setEditingStep(null)}
        onChange={(next) => {
          if (!editingStep) return;
          updateStep(editingStep.stage, editingStep.index, next);
        }}
        toolOptions={toolOptions}
        builtinOptions={builtinCatalog}
        actionTemplateOptions={actionTemplateOptions}
        readOnly={readOnly}
      />
    </div>
  );
}
