import React from 'react';
import { Link } from 'react-router-dom';

interface Task {
  id: string;
  type: string;
  startTime: string;
  deviceCount: number;
  status: 'running' | 'completed' | 'failed' | 'queued' | 'pending';
}

export const TaskList: React.FC<{ tasks: Task[] }> = ({ tasks }) => {
  const statusStyle: Record<string, string> = {
    running: 'bg-blue-100 text-blue-800',
    completed: 'bg-green-100 text-green-800',
    failed: 'bg-red-100 text-red-800',
    queued: 'bg-yellow-100 text-yellow-800',
    pending: 'bg-slate-100 text-slate-600',
  };

  if (tasks.length === 0) {
    return (
      <div className="bg-white rounded-lg shadow-sm border border-slate-200 p-8 text-center text-slate-500">
        暂无任务，请创建新任务开始测试。
      </div>
    );
  }

  return (
    <div className="bg-white rounded-lg shadow-sm border border-slate-200 overflow-hidden">
      <table className="min-w-full divide-y divide-slate-200">
        <thead className="bg-slate-50">
          <tr>
            <th className="px-6 py-3 text-left text-xs font-medium text-slate-500 uppercase tracking-wider">类型</th>
            <th className="px-6 py-3 text-left text-xs font-medium text-slate-500 uppercase tracking-wider">开始时间</th>
            <th className="px-6 py-3 text-left text-xs font-medium text-slate-500 uppercase tracking-wider">设备</th>
            <th className="px-6 py-3 text-left text-xs font-medium text-slate-500 uppercase tracking-wider">状态</th>
            <th className="px-6 py-3 text-left text-xs font-medium text-slate-500 uppercase tracking-wider">操作</th>
          </tr>
        </thead>
        <tbody className="bg-white divide-y divide-slate-200">
          {tasks.map(task => (
            <tr key={task.id} className="hover:bg-slate-50 transition-colors">
              <td className="px-6 py-4 whitespace-nowrap text-sm font-medium text-slate-900 capitalize">{task.type}</td>
              <td className="px-6 py-4 whitespace-nowrap text-sm text-slate-500">{task.startTime}</td>
              <td className="px-6 py-4 whitespace-nowrap text-sm text-slate-500">{task.deviceCount}</td>
              <td className="px-6 py-4 whitespace-nowrap">
                <span className={`px-2 inline-flex text-xs leading-5 font-semibold rounded-full ${statusStyle[task.status]}`}>
                  {task.status}
                </span>
              </td>
              <td className="px-6 py-4 whitespace-nowrap text-sm">
                <Link
                  to={`/tasks/${task.id}`}
                  className="text-blue-600 hover:text-blue-800 font-medium hover:underline"
                >
                  View Logs
                </Link>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
};
