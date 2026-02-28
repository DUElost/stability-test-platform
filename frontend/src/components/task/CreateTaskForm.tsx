import React, { useEffect, useState } from 'react';
import { Device } from '../device/DeviceCard';
import { DeviceSelector } from '../device/DeviceSelector';
import { Play, Zap } from 'lucide-react';
import { api, PipelineTemplate } from '../../utils/api';
import { PipelineEditor } from '../pipeline/PipelineEditor';
import type { PipelineDef } from '../pipeline/pipelineTypes';
import { createEmptyPipeline } from '../pipeline/pipelineTypes';

interface TaskFormProps {
  devices: Device[];
  onSubmit: (task: {
    type: string;
    deviceIds: number[];
    pipelineDef: PipelineDef;
  }) => void;
}

const DEFAULT_TASK_TYPE = 'PIPELINE';

export const CreateTaskForm: React.FC<TaskFormProps> = ({ devices, onSubmit }) => {
  const [taskType, setTaskType] = useState(DEFAULT_TASK_TYPE);
  const [selectedDevices, setSelectedDevices] = useState<number[]>([]);
  const [loading, setLoading] = useState(true);

  const [pipelineDef, setPipelineDef] = useState<PipelineDef>(createEmptyPipeline());
  const [pipelineTemplates, setPipelineTemplates] = useState<PipelineTemplate[]>([]);
  const [pipelineExpanded, setPipelineExpanded] = useState(true);

  useEffect(() => {
    const fetchTemplates = async () => {
      try {
        const tplRes = await api.tasks.listPipelineTemplates();
        setPipelineTemplates(tplRes.data);
      } catch {
        // Pipeline templates endpoint might not be available yet
      } finally {
        setLoading(false);
      }
    };
    fetchTemplates();
  }, []);

  const handleTemplateSelect = (templateName: string) => {
    const tpl = pipelineTemplates.find(t => t.name === templateName);
    if (tpl) {
      setPipelineDef(tpl.pipeline_def as PipelineDef);
      setPipelineExpanded(true);
    }
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const trimmedType = taskType.trim() || DEFAULT_TASK_TYPE;
    onSubmit({
      type: trimmedType,
      deviceIds: selectedDevices,
      pipelineDef,
    });
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20 bg-white rounded-lg shadow-sm">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-indigo-600"></div>
      </div>
    );
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-6 bg-white p-6 rounded-lg shadow-sm">
      {/* Section 1: Task Type */}
      <section>
        <div className="flex items-center gap-2 mb-3">
          <Zap size={16} className="text-slate-400" />
          <h3 className="text-sm font-semibold text-slate-500 uppercase tracking-wider">1. Task Type</h3>
        </div>
        <input
          type="text"
          value={taskType}
          onChange={(e) => setTaskType(e.target.value)}
          className="w-full border border-slate-300 rounded px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 outline-none"
          placeholder="PIPELINE"
        />
        <p className="text-xs text-slate-400 mt-2">Pipeline-only 执行，任务类型仅用于展示与统计。</p>
      </section>

      {/* Section 2: Configure Pipeline */}
      <section className="animate-in fade-in slide-in-from-top-2 duration-300">
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2">
            <Zap size={16} className="text-slate-400" />
            <h3 className="text-sm font-semibold text-slate-500 uppercase tracking-wider">2. Configure Pipeline</h3>
          </div>
          <button
            type="button"
            onClick={() => setPipelineExpanded(!pipelineExpanded)}
            className="text-slate-400 hover:text-indigo-600 transition-colors"
          >
            {pipelineExpanded ? '收起' : '展开'}
          </button>
        </div>

        {pipelineExpanded && (
          <div className="bg-slate-50 p-4 rounded-lg border border-slate-200 shadow-inner space-y-3">
            {/* Template selector */}
            {pipelineTemplates.length > 0 && (
              <div className="flex items-center gap-2 pb-3 border-b border-slate-200">
                <label className="text-xs font-medium text-slate-600 whitespace-nowrap">Load Template:</label>
                <select
                  onChange={(e) => { if (e.target.value) handleTemplateSelect(e.target.value); }}
                  className="flex-1 border border-slate-300 rounded px-2.5 py-1.5 text-sm focus:ring-2 focus:ring-indigo-500 outline-none bg-white"
                  defaultValue=""
                >
                  <option value="">Select a template...</option>
                  {pipelineTemplates.map(t => (
                    <option key={t.name} value={t.name}>{t.name} — {t.description}</option>
                  ))}
                </select>
              </div>
            )}

            <PipelineEditor
              value={pipelineDef}
              onChange={setPipelineDef}
            />
          </div>
        )}
      </section>

      {/* Section 3: Target Devices */}
      <section>
        <h3 className="text-sm font-semibold text-slate-500 uppercase tracking-wider mb-3">3. Target Devices ({selectedDevices.length})</h3>
        <DeviceSelector devices={devices} selectedDeviceIds={selectedDevices} onChange={setSelectedDevices} />
      </section>

      <div className="pt-4 border-t border-slate-100 flex justify-end">
        <button
          type="submit"
          className="bg-indigo-600 text-white px-8 py-3 rounded-lg hover:bg-indigo-700 font-semibold shadow-lg shadow-indigo-200 transition-all active:scale-95 disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
          disabled={selectedDevices.length === 0}
        >
          <Play size={18} fill="currentColor" /> Dispatch Pipeline Task
        </button>
      </div>
    </form>
  );
};
