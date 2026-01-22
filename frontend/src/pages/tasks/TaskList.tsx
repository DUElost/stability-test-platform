import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Plus } from 'lucide-react';
import { TaskList as TaskListComponent } from '../../components/task/TaskList';
import { api } from '../../utils/api';

export default function TaskList() {
  const { data: tasks, isLoading, isError } = useQuery({
    queryKey: ['tasks'],
    queryFn: () => api.tasks.list().then(res => res.data),
  });

  if (isLoading) {
    return <div className="p-8 text-center text-slate-500">Loading tasks...</div>;
  }

  if (isError) {
    return (
      <div className="p-4 bg-red-50 text-red-700 rounded-lg border border-red-200">
        Error loading tasks.
      </div>
    );
  }

  const formattedTasks = tasks?.map(t => {
    let status: 'running' | 'completed' | 'failed' = 'running';
    if (t.status === 'COMPLETED') status = 'completed';
    if (['FAILED', 'CANCELED'].includes(t.status)) status = 'failed';

    return {
      id: String(t.id),
      type: t.type,
      startTime: new Date(t.created_at).toLocaleString(),
      deviceCount: t.target_device_id ? 1 : 0,
      status
    };
  }) || [];

  return (
    <div>
      <div className="mb-6 flex justify-between items-center">
        <div>
          <h1 className="text-2xl font-bold text-slate-900">Task Management</h1>
          <p className="text-slate-500">View and manage stability test tasks</p>
        </div>
        <Link
          to="/tasks/new"
          className="bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded-lg flex items-center gap-2 transition-colors"
        >
          <Plus size={18} />
          New Task
        </Link>
      </div>
      <TaskListComponent tasks={formattedTasks} />
    </div>
  );
}
