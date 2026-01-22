import React, { useState } from 'react';
import { Device } from '../device/DeviceCard';
import { DeviceSelector } from '../device/DeviceSelector';

interface TaskFormProps {
  devices: Device[];
  onSubmit: (task: any) => void;
}

const TASK_TYPES = [
  { id: 'monkey', name: 'Monkey Stress', icon: '🐵' },
  { id: 'reboot', name: 'Reboot Loop', icon: '🔄' },
  { id: 'standby', name: 'Standby Test', icon: '🔋' },
  { id: 'camera', name: 'Camera Test', icon: '📸' },
];

export const CreateTaskForm: React.FC<TaskFormProps> = ({ devices, onSubmit }) => {
  const [taskType, setTaskType] = useState(TASK_TYPES[0].id);
  const [selectedDevices, setSelectedDevices] = useState<string[]>([]);
  const [duration, setDuration] = useState(60);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    onSubmit({
      type: taskType,
      devices: selectedDevices,
      config: { duration }
    });
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
              onClick={() => setTaskType(type.id)}
              className={`p-4 rounded-lg border text-center transition-all ${
                taskType === type.id
                  ? 'border-indigo-600 bg-indigo-50 text-indigo-700 ring-2 ring-indigo-100'
                  : 'border-slate-200 hover:border-slate-300 text-slate-600'
              }`}
            >
              <div className="text-2xl mb-1">{type.icon}</div>
              <div className="text-sm font-medium">{type.name}</div>
            </button>
          ))}
        </div>
      </section>

      <section>
        <h3 className="text-sm font-semibold text-slate-500 uppercase tracking-wider mb-3">2. Configure</h3>
        <div className="flex flex-col space-y-2">
          <label className="text-sm text-slate-700">Duration (minutes)</label>
          <input
            type="number"
            value={duration}
            onChange={e => setDuration(Number(e.target.value))}
            className="border border-slate-300 rounded px-3 py-2 w-full max-w-xs focus:outline-none focus:ring-2 focus:ring-indigo-500"
          />
        </div>
      </section>

      <section>
        <h3 className="text-sm font-semibold text-slate-500 uppercase tracking-wider mb-3">3. Target Devices ({selectedDevices.length})</h3>
        <DeviceSelector devices={devices} selectedSerials={selectedDevices} onChange={setSelectedDevices} />
      </section>

      <div className="pt-4 border-t border-slate-100 flex justify-end">
        <button type="submit" className="bg-indigo-600 text-white px-6 py-2.5 rounded hover:bg-indigo-700 font-medium disabled:opacity-50 disabled:cursor-not-allowed" disabled={selectedDevices.length === 0}>
          Dispatch Task
        </button>
      </div>
    </form>
  );
};
