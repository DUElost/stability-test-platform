import { useState } from 'react';
import { Plus, Trash2, ChevronDown, ChevronRight } from 'lucide-react';
import type { PipelineDef, PipelineStep } from '@/utils/api';
import { BUILTIN_ACTIONS } from './actionCatalog';

interface StagesPipelineEditorProps {
  value: PipelineDef;
  onChange: (def: PipelineDef) => void;
  toolOptions?: { id: number; name: string; version: string }[];
  readOnly?: boolean;
}

const STAGES: { key: keyof PipelineDef['stages']; label: string; color: string }[] = [
  { key: 'prepare',      label: 'Prepare',       color: 'border-blue-200 bg-blue-50' },
  { key: 'execute',      label: 'Execute',        color: 'border-green-200 bg-green-50' },
  { key: 'post_process', label: 'Post Process',   color: 'border-orange-200 bg-orange-50' },
];

function createEmptyStep(stageName: string, index: number): PipelineStep {  return {
    step_id: `${stageName}_step_${index + 1}`,
    action: 'builtin:check_device',
    timeout_seconds: 300,
    retry: 0,
    params: {},
  };
}

interface StepCardProps {
  step: PipelineStep;
  index: number;
  stage: string;
  onChange: (s: PipelineStep) => void;
  onRemove: () => void;
  toolOptions: { id: number; name: string; version: string }[];
  readOnly?: boolean;
}

function StepCard({ step, index, onChange, onRemove, toolOptions, readOnly }: Omit<StepCardProps, 'stage'> & { stage?: string }) {
  const [expanded, setExpanded] = useState(index === 0);
  const [paramsText, setParamsText] = useState(
    step.params && Object.keys(step.params).length > 0
      ? JSON.stringify(step.params, null, 2)
      : ''
  );
  const [paramsError, setParamsError] = useState('');

  const actionType = step.action.startsWith('tool:') ? 'tool' : 'builtin';
  const builtinName = actionType === 'builtin' ? step.action.replace('builtin:', '') : '';
  const toolId = actionType === 'tool' ? parseInt(step.action.replace('tool:', ''), 10) : 0;

  const handleActionTypeChange = (type: 'builtin' | 'tool') => {
    if (type === 'builtin') {
      onChange({ ...step, action: 'builtin:check_device', version: undefined });
    } else if (toolOptions.length > 0) {
      const first = toolOptions[0];
      onChange({ ...step, action: `tool:${first.id}`, version: first.version });
    }
  };

  const handleBuiltinChange = (name: string) => {
    onChange({ ...step, action: `builtin:${name}`, version: undefined });
  };

  const handleToolChange = (id: string) => {
    const tool = toolOptions.find(t => t.id === parseInt(id, 10));
    if (tool) onChange({ ...step, action: `tool:${tool.id}`, version: tool.version });
  };

  const handleParamsBlur = () => {
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

  return (
    <div className="border rounded-lg bg-white mb-2 shadow-sm">
      <div
        className="flex items-center gap-2 px-3 py-2 cursor-pointer select-none"
        onClick={() => setExpanded(e => !e)}
      >
        {expanded ? <ChevronDown className="w-4 h-4 text-gray-400" /> : <ChevronRight className="w-4 h-4 text-gray-400" />}
        <span className="text-xs font-mono text-gray-500 w-4">{index + 1}</span>
        <span className="flex-1 text-sm font-medium text-gray-800 truncate">{step.step_id}</span>
        <span className="text-xs px-1.5 py-0.5 rounded bg-gray-100 text-gray-500 font-mono">
          {actionType === 'tool'
            ? `tool:${toolOptions.find(t => t.id === toolId)?.name || toolId}`
            : builtinName}
        </span>
        {!readOnly && (
          <button
            className="p-1 rounded hover:bg-red-50 text-gray-300 hover:text-red-400 transition-colors"
            onClick={e => { e.stopPropagation(); onRemove(); }}
          >
            <Trash2 className="w-3.5 h-3.5" />
          </button>
        )}
      </div>

      {expanded && (
        <div className="px-3 pb-3 space-y-3 border-t">
          {/* Step ID */}
          <div className="grid grid-cols-2 gap-3 pt-3">
            <div>
              <label className="block text-xs font-medium text-gray-600 mb-1">step_id</label>
              <input
                type="text"
                value={step.step_id}
                onChange={e => onChange({ ...step, step_id: e.target.value })}
                disabled={readOnly}
                className="w-full px-2 py-1.5 text-sm border rounded focus:outline-none focus:ring-2 focus:ring-blue-500/20 font-mono"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-600 mb-1">timeout_seconds</label>
              <input
                type="number"
                min={1}
                value={step.timeout_seconds}
                onChange={e => onChange({ ...step, timeout_seconds: parseInt(e.target.value) || 300 })}
                disabled={readOnly}
                className="w-full px-2 py-1.5 text-sm border rounded focus:outline-none focus:ring-2 focus:ring-blue-500/20"
              />
            </div>
          </div>

          {/* Action */}
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">action</label>
            <div className="flex gap-2">
              <select
                value={actionType}
                onChange={e => handleActionTypeChange(e.target.value as 'builtin' | 'tool')}
                disabled={readOnly}
                className="px-2 py-1.5 text-sm border rounded focus:outline-none focus:ring-2 focus:ring-blue-500/20 bg-white"
              >
                <option value="builtin">builtin:</option>
                {toolOptions.length > 0 && <option value="tool">tool:</option>}
              </select>
              {actionType === 'builtin' ? (
                <select
                  value={builtinName}
                  onChange={e => handleBuiltinChange(e.target.value)}
                  disabled={readOnly}
                  className="flex-1 px-2 py-1.5 text-sm border rounded focus:outline-none focus:ring-2 focus:ring-blue-500/20 bg-white"
                >
                  {BUILTIN_ACTIONS.map(a => (
                    <option key={a.name} value={a.name}>{a.label} ({a.name})</option>
                  ))}
                </select>
              ) : (
                <select
                  value={toolId}
                  onChange={e => handleToolChange(e.target.value)}
                  disabled={readOnly}
                  className="flex-1 px-2 py-1.5 text-sm border rounded focus:outline-none focus:ring-2 focus:ring-blue-500/20 bg-white"
                >
                  {toolOptions.map(t => (
                    <option key={t.id} value={t.id}>{t.name} v{t.version}</option>
                  ))}
                </select>
              )}
            </div>
            {actionType === 'tool' && step.version && (
              <p className="text-xs text-gray-400 mt-1">锁定版本: v{step.version}</p>
            )}
          </div>

          {/* Retry */}
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">retry</label>
            <input
              type="number"
              min={0}
              max={5}
              value={step.retry ?? 0}
              onChange={e => onChange({ ...step, retry: parseInt(e.target.value) || 0 })}
              disabled={readOnly}
              className="w-24 px-2 py-1.5 text-sm border rounded focus:outline-none focus:ring-2 focus:ring-blue-500/20"
            />
          </div>

          {/* Params */}
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">params (JSON)</label>
            <textarea
              value={paramsText}
              onChange={e => setParamsText(e.target.value)}
              onBlur={handleParamsBlur}
              disabled={readOnly}
              rows={3}
              placeholder="{}"
              className="w-full px-2 py-1.5 text-sm border rounded focus:outline-none focus:ring-2 focus:ring-blue-500/20 font-mono resize-y"
            />
            {paramsError && <p className="text-xs text-red-500 mt-1">{paramsError}</p>}
          </div>
        </div>
      )}
    </div>
  );
}

export default function StagesPipelineEditor({
  value,
  onChange,
  toolOptions = [],
  readOnly = false,
}: StagesPipelineEditorProps) {
  const stages = value.stages ?? {};

  const updateStage = (key: keyof PipelineDef['stages'], steps: PipelineStep[]) => {
    onChange({ stages: { ...stages, [key]: steps } });
  };

  const addStep = (key: keyof PipelineDef['stages']) => {
    const current = stages[key] || [];
    updateStage(key, [...current, createEmptyStep(key, current.length)]);
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
  };

  return (
    <div className="grid grid-cols-3 gap-4">
      {STAGES.map(({ key, label, color }) => {
        const steps = stages[key] || [];
        return (
          <div key={key} className={`rounded-lg border-2 ${color} p-3`}>
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-sm font-semibold text-gray-700">{label}</h3>
              {!readOnly && (
                <button
                  className="p-1 rounded hover:bg-white/60 text-gray-500 hover:text-gray-700 transition-colors"
                  onClick={() => addStep(key)}
                  title="添加 Step"
                >
                  <Plus className="w-4 h-4" />
                </button>
              )}
            </div>

            {steps.length === 0 ? (
              <div
                className="text-center py-6 text-gray-400 text-xs border-2 border-dashed rounded-lg cursor-pointer hover:bg-white/40"
                onClick={() => !readOnly && addStep(key)}
              >
                点击添加 Step
              </div>
            ) : (
              steps.map((step, i) => (
                <StepCard
                  key={`${key}-${i}`}
                  step={step}
                  index={i}
                  stage={key}
                  onChange={s => updateStep(key, i, s)}
                  onRemove={() => removeStep(key, i)}
                  toolOptions={toolOptions}
                  readOnly={readOnly}
                />
              ))
            )}
          </div>
        );
      })}
    </div>
  );
}
