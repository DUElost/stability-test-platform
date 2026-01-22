import React from 'react';

interface Task {
  id: string;
  type: string;
  startTime: string;
  deviceCount: number;
  status: 'running' | 'completed' | 'failed';
}

export const TaskList: React.FC<{ tasks: Task[] }> = ({ tasks }) => {
  const statusStyle = {
    running: 'bg-blue-100 text-blue-800',
    completed: 'bg-green-100 text-green-800',
    failed: 'bg-red-100 text-red-800',
  };

  return (
    <div className="bg-white rounded-lg shadow-sm border border-slate-200 overflow-hidden">
      <table className="min-w-full divide-y divide-slate-200">
        <thead className="bg-slate-50">
          <tr>
            <th className="px-6 py-3 text-left text-xs font-medium text-slate-500 uppercase tracking-wider">Type</th>
            <th className="px-6 py-3 text-left text-xs font-medium text-slate-500 uppercase tracking-wider">Start Time</th>
            <th className="px-6 py-3 text-left text-xs font-medium text-slate-500 uppercase tracking-wider">Devices</th>
            <th className="px-6 py-3 text-left text-xs font-medium text-slate-500 uppercase tracking-wider">Status</th>
          </tr>
        </thead>
        <tbody className="bg-white divide-y divide-slate-200">
          {tasks.map(task => (
            <tr key={task.id}>
              <td className="px-6 py-4 whitespace-nowrap text-sm font-medium text-slate-900 capitalize">{task.type}</td>
              <td className="px-6 py-4 whitespace-nowrap text-sm text-slate-500">{task.startTime}</td>
              <td className="px-6 py-4 whitespace-nowrap text-sm text-slate-500">{task.deviceCount}</td>
              <td className="px-6 py-4 whitespace-nowrap">
                <span className={`px-2 inline-flex text-xs leading-5 font-semibold rounded-full ${statusStyle[task.status]}`}>
                  {task.status}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
};
