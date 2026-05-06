import { useState } from 'react';
import {
  DndContext, closestCenter, KeyboardSensor, PointerSensor, useSensor, useSensors,
  type DragEndEvent,
} from '@dnd-kit/core';
import {
  arrayMove, SortableContext, sortableKeyboardCoordinates, useSortable,
  verticalListSortingStrategy,
} from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { useQuery } from '@tanstack/react-query';
import { api, type ScriptEntry } from '@/utils/api';
import type { PipelinePhase, PipelineStep, PipelineDef } from '@/utils/api/types';
import {
  GripVertical, Plus, Trash2, ChevronDown, ChevronRight, Copy, Eye, Settings,
} from 'lucide-react';

// ── Types ────────────────────────────────────────────────────────────────

interface PlanLifecycleEditorProps {
  value: PipelineDef;
  onChange: (def: PipelineDef) => void;
  readOnly?: boolean;
}

interface StepCardProps {
  step: PipelineStep;
  index: number;
  readOnly: boolean;
  onUpdate: (idx: number, step: PipelineStep) => void;
  onRemove: (idx: number) => void;
  onDuplicate: (idx: number) => void;
  scripts: ScriptEntry[];
}

// ── Constants ────────────────────────────────────────────────────────────

const PHASE_LABELS: Record<PipelinePhase, string> = {
  init: '初始化',
  patrol: '巡逻',
  teardown: '清理',
};

const ALLOWED_PHASES: PipelinePhase[] = ['init', 'patrol', 'teardown'];

function createEmptyStep(scripts: ScriptEntry[], _phase: PipelinePhase, idx: number): PipelineStep {
  const firstScript = scripts.find(s => s.is_active);
  return {
    step_id: `step_${_phase}_${idx}`,
    action: firstScript ? `script:${firstScript.name}` : 'script:',
    version: firstScript?.version || '',
    params: {},
    timeout_seconds: 300,
    retry: 0,
    enabled: true,
  };
}

// ── StepCard ──────────────────────────────────────────────────────────────

function SortableStepCard({ step, index, readOnly, onUpdate, onRemove, onDuplicate, scripts }: StepCardProps) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id: step.step_id,
  });

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.5 : 1,
  };

  const scriptName = step.action.startsWith('script:') ? step.action.slice(7) : '';
  const matchedScript = scripts.find(s => s.name === scriptName && s.version === step.version);

  return (
    <div ref={setNodeRef} style={style} className="flex items-center gap-2 px-3 py-2.5 bg-white border rounded-lg group">
      <button {...attributes} {...listeners} className="text-gray-300 hover:text-gray-500 cursor-grab" disabled={readOnly}>
        <GripVertical className="w-4 h-4" />
      </button>
      <div className="flex-1 min-w-0 grid grid-cols-12 gap-2 items-center">
        <input
          className="col-span-3 px-2 py-1 text-sm border rounded focus:outline-none focus:ring-1 focus:ring-blue-500 bg-gray-50"
          value={step.step_id}
          onChange={e => onUpdate(index, { ...step, step_id: e.target.value })}
          readOnly={readOnly}
        />
        <select
          className="col-span-4 px-2 py-1 text-sm border rounded focus:outline-none focus:ring-1 focus:ring-blue-500 bg-white"
          value={`${scriptName}:${step.version || ''}`}
          onChange={e => {
            const [name, version] = e.target.value.split(':');
            onUpdate(index, { ...step, action: `script:${name}`, version: version || '', params: {} });
          }}
          disabled={readOnly}
        >
          <option value=":">— 选择脚本 —</option>
          {scripts.filter(s => s.is_active).map(s => (
            <option key={`${s.name}:${s.version}`} value={`${s.name}:${s.version}`}>
              {s.name} @ {s.version}
            </option>
          ))}
        </select>
        <input
          className="col-span-1 px-2 py-1 text-sm border rounded focus:outline-none focus:ring-1 focus:ring-blue-500 text-center"
          type="number"
          min={1}
          value={step.timeout_seconds}
          onChange={e => onUpdate(index, { ...step, timeout_seconds: Math.max(1, parseInt(e.target.value) || 300) })}
          readOnly={readOnly}
          title="超时(秒)"
        />
        <input
          className="col-span-1 px-2 py-1 text-sm border rounded focus:outline-none focus:ring-1 focus:ring-blue-500 text-center"
          type="number"
          min={0}
          max={5}
          value={step.retry ?? 0}
          onChange={e => onUpdate(index, { ...step, retry: Math.min(5, Math.max(0, parseInt(e.target.value) || 0)) })}
          readOnly={readOnly}
          title="重试次数"
        />
        <div className="col-span-3 flex items-center gap-1 text-xs text-gray-400">
          {matchedScript?.default_params ? (
            <span className="truncate" title={JSON.stringify(matchedScript.default_params)}>
              <Eye className="w-3 h-3 inline mr-1" />
              {JSON.stringify(matchedScript.default_params).slice(0, 40)}
              {(JSON.stringify(matchedScript.default_params).length > 40) ? '...' : ''}
            </span>
          ) : (
            <span className="text-gray-300">无默认参数</span>
          )}
        </div>
      </div>
      {!readOnly && (
        <div className="flex items-center gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity">
          <Button variant="ghost" size="icon" onClick={() => onDuplicate(index)} title="复制">
            <Copy className="w-3.5 h-3.5" />
          </Button>
          <Button variant="ghost" size="icon" onClick={() => onRemove(index)} className="text-red-500 hover:text-red-700" title="删除">
            <Trash2 className="w-3.5 h-3.5" />
          </Button>
        </div>
      )}
    </div>
  );
}

// ── PhaseSection ──────────────────────────────────────────────────────────

function PhaseSection({
  phase, steps, readOnly, scripts,
  onStepsChange, onAddStep,
}: {
  phase: PipelinePhase; steps: PipelineStep[]; readOnly: boolean; scripts: ScriptEntry[];
  onStepsChange: (steps: PipelineStep[]) => void;
  onAddStep: () => void;
}) {
  const [collapsed, setCollapsed] = useState(false);

  const sensors = useSensors(
    useSensor(PointerSensor),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );

  const handleDragEnd = (event: DragEndEvent) => {
    const { active, over } = event;
    if (over && active.id !== over.id) {
      const oldIndex = steps.findIndex(s => s.step_id === active.id);
      const newIndex = steps.findIndex(s => s.step_id === over.id);
      if (oldIndex !== -1 && newIndex !== -1) {
        onStepsChange(arrayMove(steps, oldIndex, newIndex));
      }
    }
  };

  const updateStep = (idx: number, updated: PipelineStep) => {
    const next = [...steps];
    next[idx] = updated;
    onStepsChange(next);
  };

  const removeStep = (idx: number) => {
    onStepsChange(steps.filter((_, i) => i !== idx));
  };

  const duplicateStep = (idx: number) => {
    const base = steps[idx];
    const copy: PipelineStep = {
      ...base,
      step_id: `${base.step_id}_copy`,
    };
    const next = [...steps];
    next.splice(idx + 1, 0, copy);
    onStepsChange(next);
  };

  return (
    <Card>
      <CardHeader className="py-3 px-4 cursor-pointer select-none" onClick={() => setCollapsed(!collapsed)}>
        <CardTitle className="text-sm flex items-center justify-between">
          <div className="flex items-center gap-2">
            {collapsed ? <ChevronRight className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
            <span>{PHASE_LABELS[phase]}</span>
            <span className="text-xs font-normal text-gray-400">({steps.length} 步骤)</span>
          </div>
          {!readOnly && (
            <Button variant="ghost" size="sm" onClick={(e) => { e.stopPropagation(); onAddStep(); }}>
              <Plus className="w-3.5 h-3.5 mr-1" /> 添加
            </Button>
          )}
        </CardTitle>
      </CardHeader>
      {!collapsed && (
        <CardContent className="pt-0 pb-3 px-4">
          {steps.length === 0 ? (
            <p className="text-sm text-gray-400 py-4 text-center">
              {phase === 'patrol' ? '无巡逻步骤（可选）' : '暂无步骤，点击"添加"按钮'}
            </p>
          ) : (
            <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
              <SortableContext items={steps.map(s => s.step_id)} strategy={verticalListSortingStrategy}>
                <div className="space-y-2">
                  {steps.map((step, idx) => (
                    <SortableStepCard
                      key={step.step_id}
                      step={step} index={idx} readOnly={readOnly}
                      onUpdate={updateStep} onRemove={removeStep} onDuplicate={duplicateStep}
                      scripts={scripts}
                    />
                  ))}
                </div>
              </SortableContext>
            </DndContext>
          )}
        </CardContent>
      )}
    </Card>
  );
}

// ── Main Component ────────────────────────────────────────────────────────

export default function PlanLifecycleEditor({ value, onChange, readOnly = false }: PlanLifecycleEditorProps) {
  const { data: scripts, isLoading: scriptsLoading } = useQuery({
    queryKey: ['scripts-active'],
    queryFn: () => api.scripts.list(true),
    staleTime: 60_000,
  });

  const lifecycle = value?.lifecycle || { init: [], teardown: [] };

  const setPhaseSteps = (phase: PipelinePhase, steps: PipelineStep[]) => {
    const next = { ...lifecycle };
    if (phase === 'patrol') {
      next.patrol = { interval_seconds: lifecycle.patrol?.interval_seconds ?? 60, steps };
    } else {
      next[phase] = steps;
    }
    onChange({ lifecycle: next });
  };

  const addStep = (phase: PipelinePhase) => {
    const steps = phase === 'patrol'
      ? (lifecycle.patrol?.steps || [])
      : (lifecycle[phase] || []);
    const newStep = createEmptyStep(scripts || [], phase, steps.length);
    setPhaseSteps(phase, [...steps, newStep]);
  };

  const handleIntervalChange = (seconds: number) => {
    const patrol = lifecycle.patrol || { interval_seconds: 60, steps: [] };
    onChange({
      lifecycle: {
        ...lifecycle,
        patrol: { ...patrol, interval_seconds: Math.max(5, seconds) },
      },
    });
  };

  if (scriptsLoading) return <Skeleton className="h-64 w-full" />;

  return (
    <div className="space-y-3">
      {ALLOWED_PHASES.map(phase => {
        const steps = phase === 'patrol' ? (lifecycle.patrol?.steps || []) : lifecycle[phase] || [];
        return (
          <div key={phase}>
            {phase === 'patrol' && (
              <div className="flex items-center gap-2 mb-2 px-1">
                <Settings className="w-3.5 h-3.5 text-gray-400" />
                <span className="text-xs text-gray-500">巡逻间隔:</span>
                <input
                  type="number" min={5}
                  value={lifecycle.patrol?.interval_seconds ?? 60}
                  onChange={e => handleIntervalChange(parseInt(e.target.value) || 60)}
                  className="w-20 px-2 py-1 text-xs border rounded focus:outline-none focus:ring-1 focus:ring-blue-500"
                  disabled={readOnly}
                />
                <span className="text-xs text-gray-400">秒</span>
              </div>
            )}
            <PhaseSection
              phase={phase} steps={steps} readOnly={readOnly}
              scripts={scripts || []}
              onStepsChange={(s) => setPhaseSteps(phase, s)}
              onAddStep={() => addStep(phase)}
            />
          </div>
        );
      })}

      {/* global timeout */}
      <div className="flex items-center gap-2 px-1">
        <span className="text-xs text-gray-500">全局超时:</span>
        <input
          type="number" min={0}
          value={lifecycle.timeout_seconds ?? 0}
          onChange={e => onChange({ lifecycle: { ...lifecycle, timeout_seconds: Math.max(0, parseInt(e.target.value) || 0) } })}
          className="w-20 px-2 py-1 text-xs border rounded focus:outline-none focus:ring-1 focus:ring-blue-500"
          disabled={readOnly}
          placeholder="0=不限"
        />
        <span className="text-xs text-gray-400">秒 (0=不限)</span>
      </div>
    </div>
  );
}
