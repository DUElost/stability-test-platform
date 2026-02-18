import { useState, useEffect } from 'react';
import { X, Plus, Trash2, GripVertical, Loader2 } from 'lucide-react';
import { api, Tool, Device, WorkflowStepCreate } from '@/utils/api';

interface StepForm {
  name: string;
  tool_id: number | null;
  params: string; // JSON string
  target_device_id: number | null;
}

interface CreateWorkflowModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSubmit: (data: { name: string; description: string; steps: WorkflowStepCreate[] }) => void;
  isSubmitting?: boolean;
}

const emptyStep = (): StepForm => ({
  name: '',
  tool_id: null,
  params: '{}',
  target_device_id: null,
});

export function CreateWorkflowModal({ isOpen, onClose, onSubmit, isSubmitting }: CreateWorkflowModalProps) {
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [steps, setSteps] = useState<StepForm[]>([emptyStep()]);
  const [errors, setErrors] = useState<Record<string, string>>({});

  const [tools, setTools] = useState<Tool[]>([]);
  const [devices, setDevices] = useState<Device[]>([]);

  useEffect(() => {
    if (isOpen) {
      setName('');
      setDescription('');
      setSteps([emptyStep()]);
      setErrors({});
      // Load tools and devices for selectors
      api.tools.list(undefined, 0, 200).then(r => setTools(r.data.items)).catch(() => {});
      api.devices.list(0, 200).then(r => setDevices(r.data.items)).catch(() => {});
    }
  }, [isOpen]);

  const addStep = () => setSteps([...steps, emptyStep()]);

  const removeStep = (index: number) => {
    if (steps.length <= 1) return;
    setSteps(steps.filter((_, i) => i !== index));
  };

  const updateStep = (index: number, field: keyof StepForm, value: any) => {
    const updated = [...steps];
    updated[index] = { ...updated[index], [field]: value };
    // Auto-fill step name from tool
    if (field === 'tool_id' && value) {
      const tool = tools.find(t => t.id === value);
      if (tool && !updated[index].name) {
        updated[index].name = tool.name;
      }
    }
    setSteps(updated);
  };

  const moveStep = (index: number, direction: -1 | 1) => {
    const target = index + direction;
    if (target < 0 || target >= steps.length) return;
    const updated = [...steps];
    [updated[index], updated[target]] = [updated[target], updated[index]];
    setSteps(updated);
  };

  const validate = (): boolean => {
    const newErrors: Record<string, string> = {};
    if (!name.trim()) newErrors.name = '请输入工作流名称';
    steps.forEach((step, i) => {
      if (!step.name.trim()) newErrors[`step_${i}_name`] = '请输入步骤名称';
      if (!step.tool_id) newErrors[`step_${i}_tool`] = '请选择工具';
      try { JSON.parse(step.params); } catch { newErrors[`step_${i}_params`] = '参数必须为有效 JSON'; }
    });
    setErrors(newErrors);
    return Object.keys(newErrors).length === 0;
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!validate()) return;
    onSubmit({
      name: name.trim(),
      description: description.trim(),
      steps: steps.map(s => ({
        name: s.name.trim(),
        tool_id: s.tool_id,
        params: JSON.parse(s.params),
        target_device_id: s.target_device_id,
      })),
    });
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50">
      <div className="bg-white rounded-lg shadow-xl w-full max-w-2xl max-h-[90vh] flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-slate-200 shrink-0">
          <h2 className="text-lg font-semibold text-slate-900">创建工作流</h2>
          <button
            onClick={onClose}
            disabled={isSubmitting}
            className="text-slate-400 hover:text-slate-600 transition-colors disabled:opacity-50"
          >
            <X size={20} />
          </button>
        </div>

        {/* Form */}
        <form onSubmit={handleSubmit} className="flex flex-col flex-1 overflow-hidden">
          <div className="overflow-y-auto p-6 space-y-5 flex-1">
            {/* Name */}
            <div>
              <label className="block text-sm font-medium text-slate-700 mb-1">
                工作流名称 <span className="text-red-500">*</span>
              </label>
              <input
                type="text"
                value={name}
                onChange={e => setName(e.target.value)}
                placeholder="例如: 完整稳定性测试"
                className={`w-full px-3 py-2 border rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 ${
                  errors.name ? 'border-red-300' : 'border-slate-300'
                }`}
                disabled={isSubmitting}
              />
              {errors.name && <p className="mt-1 text-sm text-red-600">{errors.name}</p>}
            </div>

            {/* Description */}
            <div>
              <label className="block text-sm font-medium text-slate-700 mb-1">描述</label>
              <textarea
                value={description}
                onChange={e => setDescription(e.target.value)}
                placeholder="工作流描述（可选）"
                rows={2}
                className="w-full px-3 py-2 border border-slate-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
                disabled={isSubmitting}
              />
            </div>

            {/* Steps */}
            <div>
              <div className="flex items-center justify-between mb-2">
                <label className="block text-sm font-medium text-slate-700">
                  步骤列表 <span className="text-red-500">*</span>
                </label>
                <button
                  type="button"
                  onClick={addStep}
                  disabled={isSubmitting}
                  className="flex items-center gap-1 text-sm text-blue-600 hover:text-blue-700 disabled:opacity-50"
                >
                  <Plus size={14} /> 添加步骤
                </button>
              </div>

              <div className="space-y-3">
                {steps.map((step, index) => (
                  <div key={index} className="border border-slate-200 rounded-lg p-4 bg-slate-50">
                    <div className="flex items-center gap-2 mb-3">
                      <GripVertical size={16} className="text-slate-400" />
                      <span className="text-sm font-medium text-slate-500">步骤 {index + 1}</span>
                      <div className="flex gap-1 ml-auto">
                        <button
                          type="button"
                          onClick={() => moveStep(index, -1)}
                          disabled={index === 0 || isSubmitting}
                          className="px-1.5 py-0.5 text-xs text-slate-500 hover:text-slate-700 disabled:opacity-30"
                        >
                          ↑
                        </button>
                        <button
                          type="button"
                          onClick={() => moveStep(index, 1)}
                          disabled={index === steps.length - 1 || isSubmitting}
                          className="px-1.5 py-0.5 text-xs text-slate-500 hover:text-slate-700 disabled:opacity-30"
                        >
                          ↓
                        </button>
                        <button
                          type="button"
                          onClick={() => removeStep(index)}
                          disabled={steps.length <= 1 || isSubmitting}
                          className="text-red-400 hover:text-red-600 disabled:opacity-30 ml-1"
                        >
                          <Trash2 size={14} />
                        </button>
                      </div>
                    </div>

                    <div className="grid grid-cols-2 gap-3">
                      {/* Step name */}
                      <div>
                        <label className="block text-xs text-slate-500 mb-1">名称</label>
                        <input
                          type="text"
                          value={step.name}
                          onChange={e => updateStep(index, 'name', e.target.value)}
                          placeholder="步骤名称"
                          className={`w-full px-2 py-1.5 text-sm border rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 ${
                            errors[`step_${index}_name`] ? 'border-red-300' : 'border-slate-300'
                          }`}
                          disabled={isSubmitting}
                        />
                      </div>

                      {/* Tool selector */}
                      <div>
                        <label className="block text-xs text-slate-500 mb-1">工具</label>
                        <select
                          value={step.tool_id ?? ''}
                          onChange={e => updateStep(index, 'tool_id', e.target.value ? Number(e.target.value) : null)}
                          className={`w-full px-2 py-1.5 text-sm border rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 ${
                            errors[`step_${index}_tool`] ? 'border-red-300' : 'border-slate-300'
                          }`}
                          disabled={isSubmitting}
                        >
                          <option value="">选择工具...</option>
                          {tools.filter(t => t.enabled).map(t => (
                            <option key={t.id} value={t.id}>{t.name}</option>
                          ))}
                        </select>
                      </div>

                      {/* Device selector */}
                      <div>
                        <label className="block text-xs text-slate-500 mb-1">目标设备（可选）</label>
                        <select
                          value={step.target_device_id ?? ''}
                          onChange={e => updateStep(index, 'target_device_id', e.target.value ? Number(e.target.value) : null)}
                          className="w-full px-2 py-1.5 text-sm border border-slate-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
                          disabled={isSubmitting}
                        >
                          <option value="">自动分配</option>
                          {devices.map(d => (
                            <option key={d.id} value={d.id}>{d.serial}{d.model ? ` (${d.model})` : ''}</option>
                          ))}
                        </select>
                      </div>

                      {/* Params JSON */}
                      <div>
                        <label className="block text-xs text-slate-500 mb-1">参数 (JSON)</label>
                        <input
                          type="text"
                          value={step.params}
                          onChange={e => updateStep(index, 'params', e.target.value)}
                          placeholder="{}"
                          className={`w-full px-2 py-1.5 text-sm border rounded-md font-mono focus:outline-none focus:ring-2 focus:ring-blue-500 ${
                            errors[`step_${index}_params`] ? 'border-red-300' : 'border-slate-300'
                          }`}
                          disabled={isSubmitting}
                        />
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>

          {/* Actions */}
          <div className="flex justify-end gap-3 px-6 py-4 border-t border-slate-200 shrink-0">
            <button
              type="button"
              onClick={onClose}
              disabled={isSubmitting}
              className="px-4 py-2 text-slate-700 bg-slate-100 hover:bg-slate-200 rounded-lg transition-colors disabled:opacity-50"
            >
              取消
            </button>
            <button
              type="submit"
              disabled={isSubmitting}
              className="flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg transition-colors disabled:opacity-50"
            >
              {isSubmitting ? (
                <>
                  <Loader2 size={18} className="animate-spin" />
                  创建中...
                </>
              ) : (
                '创建工作流'
              )}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
