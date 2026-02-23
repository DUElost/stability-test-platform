import React, { useEffect, useState } from 'react';
import { Device } from '../device/DeviceCard';
import { DeviceSelector } from '../device/DeviceSelector';
import { FileJson, Play, Wrench, Zap, ChevronDown, ChevronRight } from 'lucide-react';
import { api, Tool, ToolCategory, PipelineTemplate } from '../../utils/api';
import { ToolSelector } from './ToolSelector';
import { DynamicToolForm } from './DynamicToolForm';
import { PipelineEditor } from '../pipeline/PipelineEditor';
import type { PipelineDef } from '../pipeline/pipelineTypes';
import { createEmptyPipeline } from '../pipeline/pipelineTypes';

interface TaskFormProps {
  devices: Device[];
  onSubmit: (task: {
    type: string;
    deviceIds: number[];
    config: Record<string, any>;
    pipelineDef?: PipelineDef;
  }) => void;
}

const cloneConfig = (value: Record<string, any>): Record<string, any> =>
  JSON.parse(JSON.stringify(value || {}));

export const CreateTaskForm: React.FC<TaskFormProps> = ({ devices, onSubmit }) => {
  const [tools, setTools] = useState<Tool[]>([]);
  const [categories, setCategories] = useState<ToolCategory[]>([]);
  const [selectedTool, setSelectedTool] = useState<Tool | null>(null);
  const [selectedDevices, setSelectedDevices] = useState<number[]>([]);
  const [config, setConfig] = useState<Record<string, any>>({});
  const [showJson, setShowJson] = useState(false);
  const [loading, setLoading] = useState(true);

  // Pipeline editor state
  const [usePipeline, setUsePipeline] = useState(false);
  const [pipelineDef, setPipelineDef] = useState<PipelineDef>(createEmptyPipeline());
  const [pipelineTemplates, setPipelineTemplates] = useState<PipelineTemplate[]>([]);
  const [pipelineExpanded, setPipelineExpanded] = useState(true);

  useEffect(() => {
    const fetchData = async () => {
      try {
        const [toolsRes, catRes] = await Promise.all([
          api.tools.list(undefined, 0, 200),
          api.tools.listCategories(0, 200)
        ]);
        setTools(toolsRes.data.items);
        setCategories(catRes.data.items);

        // Auto-select first tool if available
        if (toolsRes.data.items.length > 0) {
          const firstTool = toolsRes.data.items[0];
          setSelectedTool(firstTool);
          setConfig(cloneConfig(firstTool.default_params));
        }

        // Fetch pipeline templates (non-blocking)
        try {
          const tplRes = await api.tasks.listPipelineTemplates();
          setPipelineTemplates(tplRes.data);
        } catch {
          // Pipeline templates endpoint might not be available yet
        }
      } catch (error) {
        console.error('Failed to fetch tools:', error);
      } finally {
        setLoading(false);
      }
    };
    fetchData();
  }, []);

  const handleToolSelect = (tool: Tool) => {
    setSelectedTool(tool);
    setConfig(cloneConfig(tool.default_params));
  };

  const handleConfigChange = (key: string, value: any) => {
    setConfig((prev: any) => ({ ...prev, [key]: value }));
  };

  const handleTemplateSelect = (templateName: string) => {
    const tpl = pipelineTemplates.find(t => t.name === templateName);
    if (tpl) {
      setPipelineDef(tpl.pipeline_def as PipelineDef);
      setUsePipeline(true);
      setPipelineExpanded(true);
    }
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedTool) return;

    // Build the final params including tool metadata for the agent
    const finalParams = {
      ...config,
      tool_id: selectedTool.id,
      script_path: selectedTool.script_path,
      script_class: selectedTool.script_class,
      script_type: selectedTool.script_type,
      default_params: selectedTool.default_params,
    };

    onSubmit({
      type: selectedTool.name,
      deviceIds: selectedDevices,
      config: finalParams,
      pipelineDef: usePipeline ? pipelineDef : undefined,
    });
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20 bg-white rounded-lg shadow-sm">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-indigo-600"></div>
      </div>
    );
  }

  // Map tools to the format PipelineEditor expects
  const editorTools = tools.map(t => ({
    id: t.id,
    name: t.name,
    description: t.description,
    param_schema: t.param_schema,
  }));

  return (
    <form onSubmit={handleSubmit} className="space-y-6 bg-white p-6 rounded-lg shadow-sm">
      {/* Section 1: Select Tool */}
      <section>
        <div className="flex items-center gap-2 mb-3">
          <Wrench size={16} className="text-slate-400" />
          <h3 className="text-sm font-semibold text-slate-500 uppercase tracking-wider">1. Select Tool</h3>
        </div>
        <ToolSelector
          tools={tools}
          categories={categories}
          selectedToolId={selectedTool?.id || null}
          onSelect={handleToolSelect}
        />
      </section>

      {/* Section 2: Configuration */}
      {selectedTool && (
        <section className="animate-in fade-in slide-in-from-top-2 duration-300">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-semibold text-slate-500 uppercase tracking-wider">2. Configuration: {selectedTool.name}</h3>
            <button type="button" onClick={() => setShowJson(!showJson)} className="text-slate-400 hover:text-indigo-600 transition-colors">
               <FileJson size={16} />
            </button>
          </div>

          {showJson ? (
             <pre className="bg-slate-50 p-4 rounded text-xs font-mono overflow-auto border border-slate-200 max-h-60">
               {JSON.stringify(config, null, 2)}
             </pre>
          ) : (
            <div className="bg-slate-50 p-6 rounded-lg border border-slate-200 shadow-inner">
              <DynamicToolForm
                schema={selectedTool.param_schema}
                values={config}
                onChange={handleConfigChange}
              />
            </div>
          )}
        </section>
      )}

      {/* Section 3: Configure Pipeline (optional) */}
      {selectedTool && (
        <section className="animate-in fade-in slide-in-from-top-2 duration-300">
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2">
              <Zap size={16} className="text-slate-400" />
              <h3 className="text-sm font-semibold text-slate-500 uppercase tracking-wider">3. Configure Pipeline</h3>
              <span className="text-[10px] px-1.5 py-0.5 bg-slate-100 text-slate-500 rounded-full font-medium uppercase">Optional</span>
            </div>
            <div className="flex items-center gap-2">
              <label className="flex items-center gap-1.5 cursor-pointer">
                <input
                  type="checkbox"
                  checked={usePipeline}
                  onChange={(e) => setUsePipeline(e.target.checked)}
                  className="h-3.5 w-3.5 rounded border-slate-300 text-indigo-600 focus:ring-indigo-500"
                />
                <span className="text-xs text-slate-500">Enable Pipeline</span>
              </label>
              {usePipeline && (
                <button type="button" onClick={() => setPipelineExpanded(!pipelineExpanded)} className="text-slate-400 hover:text-indigo-600 transition-colors">
                  {pipelineExpanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
                </button>
              )}
            </div>
          </div>

          {usePipeline && pipelineExpanded && (
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
                tools={editorTools}
              />
            </div>
          )}

          {!usePipeline && (
            <p className="text-xs text-slate-400 italic">
              Enable pipeline mode to define custom execution phases and steps. Without pipeline, the tool runs using its default lifecycle.
            </p>
          )}
        </section>
      )}

      {/* Section 4: Target Devices */}
      <section>
        <h3 className="text-sm font-semibold text-slate-500 uppercase tracking-wider mb-3">{selectedTool ? '4' : '3'}. Target Devices ({selectedDevices.length})</h3>
        <DeviceSelector devices={devices} selectedDeviceIds={selectedDevices} onChange={setSelectedDevices} />
      </section>

      <div className="pt-4 border-t border-slate-100 flex justify-end">
        <button
          type="submit"
          className="bg-indigo-600 text-white px-8 py-3 rounded-lg hover:bg-indigo-700 font-semibold shadow-lg shadow-indigo-200 transition-all active:scale-95 disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
          disabled={selectedDevices.length === 0 || !selectedTool}
        >
          <Play size={18} fill="currentColor" /> Dispatch Tool Task
        </button>
      </div>
    </form>
  );
};
