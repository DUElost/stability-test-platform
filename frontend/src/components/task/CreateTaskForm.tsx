import React, { useState, useEffect } from 'react';
import { Device } from '../device/DeviceCard';
import { DeviceSelector } from '../device/DeviceSelector';
import { FileJson, Play } from 'lucide-react';

interface TaskFormProps {
  devices: Device[];
  onSubmit: (task: any) => void;
}

type TaskType = 'MONKEY' | 'AIMONKEY' | 'MTBF' | 'DDR' | 'GPU' | 'STANDBY';

const TASK_TYPES = [
  { id: 'MONKEY', name: 'Monkey Stress', icon: '🐵', desc: 'Random event stress test' },
  { id: 'AIMONKEY', name: 'AI Monkey', icon: '🤖', desc: 'Intelligent stress test with storage fill' },
  { id: 'MTBF', name: 'MTBF Test', icon: '⚡', desc: 'Mean Time Between Failures' },
  { id: 'DDR', name: 'DDR Memory', icon: '💾', desc: 'Memory stress test' },
  { id: 'GPU', name: 'GPU Stress', icon: '🎮', desc: 'Graphics performance test' },
  { id: 'STANDBY', name: 'Standby Power', icon: '🔋', desc: 'Power consumption test' },
];

const DEFAULT_CONFIGS: Record<TaskType, any> = {
  MONKEY: { package: '', event_count: 10000, throttle: 300, seed: 0 },
  AIMONKEY: {
    runtime_minutes: 60,
    throttle_ms: 500,
    max_restarts: 1,
    enable_fill_storage: false,
    enable_clear_logs: false,
    wifi_ssid: '',
    wifi_password: '',
    target_fill_percentage: 60,
    run_id: ''
  },
  MTBF: { resource_dir: '', remote_dir: '/data/local/tmp', apk_path: '', runner: '', instrument_args: '' },
  DDR: { memtester_path: '/data/local/tmp/memtester', remote_path: '', mem_size_mb: 100, loops: 1 },
  GPU: { apk_path: '', activity: '', loops: 10, interval: 1000 },
  STANDBY: { video_url: '', standby_seconds: 60, screen_off: true },
};

export const CreateTaskForm: React.FC<TaskFormProps> = ({ devices, onSubmit }) => {
  const [taskType, setTaskType] = useState<TaskType>('MONKEY');
  const [selectedDevices, setSelectedDevices] = useState<string[]>([]);
  const [config, setConfig] = useState(DEFAULT_CONFIGS.MONKEY);
  const [showJson, setShowJson] = useState(false);

  useEffect(() => {
    setConfig(DEFAULT_CONFIGS[taskType]);
  }, [taskType]);

  const handleConfigChange = (key: string, value: any) => {
    setConfig((prev: any) => ({ ...prev, [key]: value }));
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    onSubmit({
      type: taskType,
      devices: selectedDevices,
      config
    });
  };

  const renderConfigFields = () => {
    switch (taskType) {
      case 'MONKEY':
        return (
          <>
            <div className="grid grid-cols-2 gap-4">
              <div className="col-span-2">
                <label className="block text-xs font-medium text-slate-700 mb-1">Package Name <span className="text-red-500">*</span></label>
                <input
                  type="text"
                  required
                  value={config.package || ''}
                  onChange={e => handleConfigChange('package', e.target.value)}
                  placeholder="com.example.app"
                  className="w-full border border-slate-300 rounded px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 outline-none"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-slate-700 mb-1">Event Count</label>
                <input
                  type="number"
                  value={config.event_count}
                  onChange={e => handleConfigChange('event_count', parseInt(e.target.value))}
                  className="w-full border border-slate-300 rounded px-3 py-2 text-sm outline-none"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-slate-700 mb-1">Throttle (ms)</label>
                <input
                  type="number"
                  value={config.throttle}
                  onChange={e => handleConfigChange('throttle', parseInt(e.target.value))}
                  className="w-full border border-slate-300 rounded px-3 py-2 text-sm outline-none"
                />
              </div>
            </div>
          </>
        );
      case 'AIMONKEY':
        return (
          <div className="space-y-4">
            <div className="grid grid-cols-3 gap-4">
              <div>
                <label className="block text-xs font-medium text-slate-700 mb-1">Runtime (min) <span className="text-red-500">*</span></label>
                <input
                  type="number"
                  required
                  min={1}
                  value={config.runtime_minutes}
                  onChange={e => handleConfigChange('runtime_minutes', parseInt(e.target.value))}
                  className="w-full border border-slate-300 rounded px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-indigo-500"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-slate-700 mb-1">Throttle (ms)</label>
                <input
                  type="number"
                  value={config.throttle_ms}
                  onChange={e => handleConfigChange('throttle_ms', parseInt(e.target.value))}
                  className="w-full border border-slate-300 rounded px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-indigo-500"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-slate-700 mb-1">Max Restarts</label>
                <input
                  type="number"
                  min={0}
                  value={config.max_restarts}
                  onChange={e => handleConfigChange('max_restarts', parseInt(e.target.value))}
                  className="w-full border border-slate-300 rounded px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-indigo-500"
                />
              </div>
            </div>

            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-xs font-medium text-slate-700 mb-1">WiFi SSID</label>
                <input
                  type="text"
                  value={config.wifi_ssid}
                  onChange={e => handleConfigChange('wifi_ssid', e.target.value)}
                  placeholder="Optional"
                  className="w-full border border-slate-300 rounded px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-indigo-500"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-slate-700 mb-1">WiFi Password</label>
                <input
                  type="text"
                  value={config.wifi_password}
                  onChange={e => handleConfigChange('wifi_password', e.target.value)}
                  placeholder="Optional"
                  className="w-full border border-slate-300 rounded px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-indigo-500"
                />
              </div>
            </div>

            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-xs font-medium text-slate-700 mb-1">Run ID</label>
                <input
                  type="text"
                  value={config.run_id}
                  onChange={e => handleConfigChange('run_id', e.target.value)}
                  placeholder="Optional"
                  className="w-full border border-slate-300 rounded px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-indigo-500"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-slate-700 mb-1">Target Fill %</label>
                <input
                  type="number"
                  min={0}
                  max={100}
                  value={config.target_fill_percentage}
                  onChange={e => handleConfigChange('target_fill_percentage', parseInt(e.target.value))}
                  disabled={!config.enable_fill_storage}
                  className={`w-full border border-slate-300 rounded px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-indigo-500 ${!config.enable_fill_storage ? 'bg-slate-100 text-slate-400' : ''}`}
                />
              </div>
            </div>

            <div className="flex items-center gap-6 pt-2">
              <div className="flex items-center">
                <input
                  type="checkbox"
                  id="enable_fill_storage"
                  checked={config.enable_fill_storage}
                  onChange={e => handleConfigChange('enable_fill_storage', e.target.checked)}
                  className="rounded border-slate-300 text-indigo-600 focus:ring-indigo-500 h-4 w-4"
                />
                <label htmlFor="enable_fill_storage" className="ml-2 text-sm text-slate-700 cursor-pointer">Fill Storage</label>
              </div>
              <div className="flex items-center">
                <input
                  type="checkbox"
                  id="enable_clear_logs"
                  checked={config.enable_clear_logs}
                  onChange={e => handleConfigChange('enable_clear_logs', e.target.checked)}
                  className="rounded border-slate-300 text-indigo-600 focus:ring-indigo-500 h-4 w-4"
                />
                <label htmlFor="enable_clear_logs" className="ml-2 text-sm text-slate-700 cursor-pointer">Clear Logs</label>
              </div>
            </div>
          </div>
        );
      case 'MTBF':
        return (
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-4">
              <div className="col-span-2">
                <label className="block text-xs font-medium text-slate-700 mb-1">Resource Directory</label>
                <input type="text" value={config.resource_dir} onChange={e => handleConfigChange('resource_dir', e.target.value)} className="w-full border border-slate-300 rounded px-3 py-2 text-sm" />
              </div>
              <div>
                <label className="block text-xs font-medium text-slate-700 mb-1">Test Runner</label>
                <input type="text" value={config.runner} onChange={e => handleConfigChange('runner', e.target.value)} className="w-full border border-slate-300 rounded px-3 py-2 text-sm" />
              </div>
              <div>
                <label className="block text-xs font-medium text-slate-700 mb-1">Instrument Args</label>
                <input type="text" value={config.instrument_args} onChange={e => handleConfigChange('instrument_args', e.target.value)} className="w-full border border-slate-300 rounded px-3 py-2 text-sm" />
              </div>
            </div>
          </div>
        );
      case 'DDR':
        return (
          <div className="grid grid-cols-2 gap-4">
            <div className="col-span-2">
               <label className="block text-xs font-medium text-slate-700 mb-1">Memtester Path</label>
               <input type="text" value={config.memtester_path} onChange={e => handleConfigChange('memtester_path', e.target.value)} className="w-full border border-slate-300 rounded px-3 py-2 text-sm" />
            </div>
            <div>
               <label className="block text-xs font-medium text-slate-700 mb-1">Memory Size (MB)</label>
               <input type="number" value={config.mem_size_mb} onChange={e => handleConfigChange('mem_size_mb', parseInt(e.target.value))} className="w-full border border-slate-300 rounded px-3 py-2 text-sm" />
            </div>
            <div>
               <label className="block text-xs font-medium text-slate-700 mb-1">Loops</label>
               <input type="number" value={config.loops} onChange={e => handleConfigChange('loops', parseInt(e.target.value))} className="w-full border border-slate-300 rounded px-3 py-2 text-sm" />
            </div>
          </div>
        );
      case 'GPU':
         return (
          <div className="grid grid-cols-2 gap-4">
             <div className="col-span-2">
                <label className="block text-xs font-medium text-slate-700 mb-1">APK Path</label>
                <input type="text" value={config.apk_path} onChange={e => handleConfigChange('apk_path', e.target.value)} className="w-full border border-slate-300 rounded px-3 py-2 text-sm" />
             </div>
             <div>
                <label className="block text-xs font-medium text-slate-700 mb-1">Target Activity</label>
                <input type="text" value={config.activity} onChange={e => handleConfigChange('activity', e.target.value)} className="w-full border border-slate-300 rounded px-3 py-2 text-sm" />
             </div>
             <div>
                <label className="block text-xs font-medium text-slate-700 mb-1">Interval (ms)</label>
                <input type="number" value={config.interval} onChange={e => handleConfigChange('interval', parseInt(e.target.value))} className="w-full border border-slate-300 rounded px-3 py-2 text-sm" />
             </div>
          </div>
         );
      case 'STANDBY':
        return (
          <div className="space-y-3">
            <div>
               <label className="block text-xs font-medium text-slate-700 mb-1">Video URL (Optional)</label>
               <input type="text" value={config.video_url} onChange={e => handleConfigChange('video_url', e.target.value)} className="w-full border border-slate-300 rounded px-3 py-2 text-sm" />
            </div>
            <div className="flex items-center gap-4">
               <div>
                  <label className="block text-xs font-medium text-slate-700 mb-1">Duration (sec)</label>
                  <input type="number" value={config.standby_seconds} onChange={e => handleConfigChange('standby_seconds', parseInt(e.target.value))} className="w-full border border-slate-300 rounded px-3 py-2 text-sm w-32" />
               </div>
               <div className="flex items-center pt-5">
                  <input
                    type="checkbox"
                    id="screen_off"
                    checked={config.screen_off}
                    onChange={e => handleConfigChange('screen_off', e.target.checked)}
                    className="rounded border-slate-300 text-indigo-600 focus:ring-indigo-500"
                  />
                  <label htmlFor="screen_off" className="ml-2 text-sm text-slate-700">Screen Off</label>
               </div>
            </div>
          </div>
        );
      default:
        return null;
    }
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-6 bg-white p-6 rounded-lg shadow-sm">
      <section>
        <h3 className="text-sm font-semibold text-slate-500 uppercase tracking-wider mb-3">1. Select Task Type</h3>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          {TASK_TYPES.map(type => (
            <button
              key={type.id}
              type="button"
              onClick={() => setTaskType(type.id as TaskType)}
              className={`p-4 rounded-lg border text-center transition-all ${
                taskType === type.id
                  ? 'border-indigo-600 bg-indigo-50 text-indigo-700 ring-2 ring-indigo-100'
                  : 'border-slate-200 hover:border-slate-300 text-slate-600'
              }`}
            >
              <div className="text-2xl mb-1">{type.icon}</div>
              <div className="text-sm font-medium">{type.name}</div>
              <div className="text-[10px] opacity-75 mt-1">{type.desc}</div>
            </button>
          ))}
        </div>
      </section>

      <section>
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold text-slate-500 uppercase tracking-wider">2. Configuration</h3>
          <button type="button" onClick={() => setShowJson(!showJson)} className="text-slate-400 hover:text-indigo-600">
             <FileJson size={16} />
          </button>
        </div>

        {showJson ? (
           <pre className="bg-slate-50 p-4 rounded text-xs font-mono overflow-auto border border-slate-200 max-h-60">
             {JSON.stringify(config, null, 2)}
           </pre>
        ) : (
          <div className="bg-slate-50 p-4 rounded border border-slate-200">
            {renderConfigFields()}
          </div>
        )}
      </section>

      <section>
        <h3 className="text-sm font-semibold text-slate-500 uppercase tracking-wider mb-3">3. Target Devices ({selectedDevices.length})</h3>
        <DeviceSelector devices={devices} selectedSerials={selectedDevices} onChange={setSelectedDevices} />
      </section>

      <div className="pt-4 border-t border-slate-100 flex justify-end">
        <button type="submit" className="bg-indigo-600 text-white px-6 py-2.5 rounded hover:bg-indigo-700 font-medium disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2" disabled={selectedDevices.length === 0}>
          <Play size={18} /> Dispatch Task
        </button>
      </div>
    </form>
  );
};
