import React, { useEffect, useState } from 'react';
import { Device } from '../device/DeviceCard';
import { DeviceSelector } from '../device/DeviceSelector';
import { FileJson, Play, Wrench } from 'lucide-react';
import { api, Tool, ToolCategory } from '../../utils/api';
import { ToolSelector } from './ToolSelector';
import { DynamicToolForm } from './DynamicToolForm';

interface TaskFormProps {
  devices: Device[];
  onSubmit: (task: {
    type: string;
    deviceIds: number[];
    config: Record<string, any>;
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

  useEffect(() => {
    const fetchData = async () => {
      try {
        const [toolsRes, catRes] = await Promise.all([
          api.tools.list(undefined),
          api.tools.listCategories()
        ]);
        setTools(toolsRes.data);
        setCategories(catRes.data);
        
        // Auto-select first tool if available
        if (toolsRes.data.length > 0) {
          const firstTool = toolsRes.data[0];
          setSelectedTool(firstTool);
          setConfig(cloneConfig(firstTool.default_params));
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

      <section>
        <h3 className="text-sm font-semibold text-slate-500 uppercase tracking-wider mb-3">3. Target Devices ({selectedDevices.length})</h3>
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

